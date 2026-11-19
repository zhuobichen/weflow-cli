"""路由集：9 个闭环选项。

为什么要有 `unknown`：没有它，模型只能在 8 个选项里强选一个。
"帮我写个周报"会被塞进"统计"——错得理直气壮，比弃权更糟。

criteria 文本是**给模型看的判据**，不是给人看的菜单。写得含糊，它就乱选；
所以每条都写"什么时候选它"和"什么时候别选它"。
"""
from __future__ import annotations

ROUTES = {
    'promises': {
        'label': '查账（承诺 / 待办）',
        'criteria': (
            '用户想知道自己答应过别人什么、有没有兑现、欠谁东西、谁在等他回话。'
            '关键词：答应、承诺、欠、还没发、待办、兑现、催。'
            '不选：只是想找某条聊天原文（那是 search_chats）。'
        ),
    },
    'search_chats': {
        'label': '查聊天记录',
        'criteria': (
            '用户要按内容或时间找回某段对话、某条消息、谁说过什么。'
            '关键词：找一下、谁提过、那天说的、聊天记录里。'
            '不选：问阅读/文章（那是 search_knowledge）；问承诺是否兑现（那是 promises）。'
        ),
    },
    'search_knowledge': {
        'label': '查阅读 / 知识库',
        'criteria': (
            '用户问公众号文章、收藏、笔记、概念、某个观点在哪篇里。'
            '关键词：文章、收藏、笔记、读到过、观点、概念。'
            '不选：问微信聊天内容。'
        ),
    },
    'relations': {
        'label': '查关系 / 人脉',
        'criteria': (
            '用户问和某人的关系、共同群、谁认识谁、关系是否变淡、能找谁引荐。'
            '关键词：共同群、认识、引荐、关系、多久没聊。'
            '不选：单纯查某人资料（那是 contacts）。'
        ),
    },
    'export': {
        'label': '导出会话',
        'criteria': (
            '用户要把聊天导出成文件（HTML / Markdown / Excel / JSON / TXT）、要备份、要留档。'
            '关键词：导出、备份、存成、发给别人一份。'
            '不选：只是想看内容（那是 search_chats）。'
        ),
    },
    'daily': {
        'label': '公众号日报',
        'criteria': (
            '用户要今天的/某天的公众号简报、日报、阅读汇总。'
            '关键词：日报、简报、今天推了什么、公众号汇总。'
            '不选：问某篇文章内容（那是 search_knowledge）。'
        ),
    },
    'stats': {
        'label': '统计',
        'criteria': (
            '用户要数量、频次、时长、排行这类聚合数字。'
            '关键词：多少条、多久、排名、统计、占用。'
        ),
    },
    'contacts': {
        'label': '联系人资料',
        'criteria': (
            '用户查某人的昵称、备注、微信号、头像这类静态资料。'
            '关键词：微信号、昵称、备注、叫什么。'
            '不选：问与他的互动（那是 relations）。'
        ),
    },
    'unknown': {
        'label': '无法归类',
        'criteria': (
            '这个问题不是关于用户自己的微信历史的：写作、翻译、常识问答、天气、代码、'
            '闲聊，或者信息不足以判断。**宁可选它，也不要硬塞进上面任何一个。**'
        ),
    },
}

ROUTE_IDS = list(ROUTES)


# 规则臂：关键词命中。故意做得朴素——它的作用是当下界基线，
# 证明"判断模型确实比正则强"，而不是要赢。
RULE_PATTERNS = [
    ('export', ['导出', '备份', '存成', '保存为', '导成']),
    ('daily', ['日报', '简报', '今天推了', '公众号汇总']),
    ('promises', ['答应', '承诺', '欠', '还没发', '兑现', '催', '待办', '回话']),
    ('relations', ['共同群', '引荐', '关系', '多久没聊', '认识谁', '变淡']),
    ('contacts', ['微信号', '昵称', '备注', '叫什么', '头像']),
    ('stats', ['多少条', '统计', '排名', '多久', '占用']),
    ('search_knowledge', ['文章', '收藏', '笔记', '读到过', '观点', '概念', '公众号']),
    ('search_chats', ['找一下', '谁提过', '那天', '聊天记录', '说过', '消息']),
]


def rule_route(question: str) -> tuple[str, str]:
    """返回 (route, 命中的关键词)。一条都不命中 → unknown。"""
    for route, words in RULE_PATTERNS:
        for w in words:
            if w in question:
                return route, w
    return 'unknown', ''


# ── 分权：哪一层可以出网 ────────────────────────────────────────────
# 阶段 → 发出去的是什么。路由只发**问题**（几十字），判定与生成要发**原话**。
STAGES = {
    'route': {'what_leaves': '用户问题（不含聊天内容）', 'options': ['cloud', 'local', 'rule'],
              'default': 'cloud'},
    'judge': {'what_leaves': '聊天原话（证据块）', 'options': ['local', 'cloud'],
              'default': 'local'},
    'compose': {'what_leaves': '证据块', 'options': ['local', 'cloud'], 'default': 'local'},
}

PROFILES = {
    'balanced': {'route': 'cloud', 'judge': 'local', 'compose': 'local'},
    'offline': {'route': 'local', 'judge': 'local', 'compose': 'local'},
    'cloud': {'route': 'cloud', 'judge': 'cloud', 'compose': 'cloud'},
}


def resolve(overrides=None):
    out = {stage: cfg['default'] for stage, cfg in STAGES.items()}
    for stage, value in (overrides or {}).items():
        if stage not in STAGES:
            raise KeyError('未知阶段 %r；只有 %s' % (stage, list(STAGES)))
        if value not in STAGES[stage]['options']:
            raise ValueError('%s 不支持 %r；可选 %s'
                             % (stage, value, STAGES[stage]['options']))
        out[stage] = value
    return out


def profile(name):
    """按档位取三个阶段各自的通路。"""
    if name not in PROFILES:
        raise KeyError('未知档位 %r；可选 %s' % (name, list(PROFILES)))
    return resolve(PROFILES[name])


def describe(resolved):
    lines = []
    for stage, provider in resolved.items():
        lines.append('  %-8s %-6s  发送内容：%s'
                     % (stage, provider, STAGES[stage]['what_leaves']))
    return chr(10).join(lines)
