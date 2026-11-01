"""校验 docs/VERSION_METHODS.md：失效的方法必须写清"为什么"和"换成什么"。

为什么需要这条测试：这张表是**唯一事实源**，但它靠人维护。
本项目已经踩过一次：某个方法在 4.2 上失效，程序却报"微信没在运行"，
用户被引导去重装依赖。**失效登记漏填原因/替代**，下一个版本就会原样复发。

规则：
  1. ID 唯一且形如 M-NN；
  2. 状态为"失效"的行，`失效原因` 与 `替代方法` 都不能为空；
  3. 每一行都必须带证据日期（YYYY-MM-DD）；
  4. 表至少要有 1 行"可用"和 1 行"失效"——退化匹配的护栏：
     如果表格被清空或只剩一种状态，这条测试要红，而不是"通过"。
"""
import pathlib
import re
import unittest

DOC = pathlib.Path(__file__).resolve().parent.parent / 'docs' / 'VERSION_METHODS.md'
DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
ID_RE = re.compile(r'^M-\d{2}$')


def parse_rows(text):
    """把 markdown 表格解析成字典列表（只认以 | M- 开头的行）。"""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('| M-'):
            continue
        # 单元格里允许转义的竖线（正则里常有），先换占位符再切分；
        # 否则一个正则就把整行切错位——守卫测试自己就踩过。
        PROTECT = '\x00PIPE\x00'
        cells = [c.strip().replace(PROTECT, '|')
                 for c in line.replace('\\|', PROTECT).strip('|').split('|')]
        if len(cells) < 7:
            continue
        rows.append({
            'id': cells[0], 'method': cells[1], 'version': cells[2],
            'status': cells[3], 'reason': cells[4], 'replacement': cells[5],
            'evidence': cells[6],
        })
    return rows


class VersionMethodTableTests(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding='utf-8')
        self.rows = parse_rows(self.text)

    def test_doc_exists_and_rows_parsed(self):
        self.assertTrue(DOC.exists(), 'docs/VERSION_METHODS.md 不存在')
        self.assertGreaterEqual(len(self.rows), 5,
                                f'只解析到 {len(self.rows)} 行——表格格式或内容不对')

    def test_ids_unique_and_wellformed(self):
        ids = [r['id'] for r in self.rows]
        for r in self.rows:
            self.assertRegex(r['id'], ID_RE, f'ID 格式不对: {r["id"]}')
        self.assertEqual(len(ids), len(set(ids)), f'ID 重复: {ids}')

    def test_failed_rows_explain_why_and_what_replaced_it(self):
        failed = [r for r in self.rows if '失效' in r['status']]
        self.assertTrue(failed, '一行"失效"都没有——表格退化了，不能算通过')
        for r in failed:
            self.assertTrue(r['reason'] and r['reason'] not in ('—', '-'),
                            f'{r["id"]} 标为失效但没写原因')
            self.assertTrue(r['replacement'],
                            f'{r["id"]} 标为失效但没写替代方法（没有就写"无"+理由）')

    def test_every_row_has_evidence_date(self):
        for r in self.rows:
            self.assertTrue(DATE_RE.search(r['evidence']),
                            f'{r["id"]} 缺少证据日期（YYYY-MM-DD）')

    def test_every_row_names_a_version_scope(self):
        for r in self.rows:
            self.assertTrue(r['version'] and r['version'] not in ('—', '-'),
                            f'{r["id"]} 没写适用版本')

    def test_table_has_both_states(self):
        """退化护栏：只剩一种状态说明表被写坏了，而不是"全都可用"。"""
        statuses = ' '.join(r['status'] for r in self.rows)
        self.assertIn('可用', statuses)
        self.assertIn('失效', statuses)


if __name__ == '__main__':
    unittest.main()
