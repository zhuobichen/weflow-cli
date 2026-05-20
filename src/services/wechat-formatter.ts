/**
 * 微信公众号 Markdown → HTML 排版转换器
 *
 * 基于 wechat-publisher-mcp 的 MarkdownConverter.js（MIT 许可）改写，
 * 增加多主题支持，输出微信兼容的 inline-style HTML。
 */
export interface FormatOptions {
  theme?: 'default' | 'warm' | 'minimal' | 'green';
  fontSize?: number;
  lineHeight?: number;
}

interface ThemeConfig {
  id: string;
  name: string;
  description: string;
  h1: { color: string; border: string };
  h2: { color: string; prefix: string };
  h3: { color: string; prefix: string };
  h4: { color: string; prefix: string };
  bold: { color: string };
  italic: { color: string };
  del: { color: string };
  codeBlock: { bg: string; border: string };
  inlineCode: { bg: string; color: string };
  blockquote: { border: string; bg: string; color: string };
  link: { color: string };
}

const THEMES: Record<string, ThemeConfig> = {
  default: {
    id: 'default',
    name: '经典蓝',
    description: '蓝色强调 + 红色重点，适配大多数内容',
    h1: { color: '#2c3e50', border: '#3498db' },
    h2: { color: '#3498db', prefix: '🔹' },
    h3: { color: '#27ae60', prefix: '▶' },
    h4: { color: '#8e44ad', prefix: '•' },
    bold: { color: '#e74c3c' },
    italic: { color: '#9b59b6' },
    del: { color: '#95a5a6' },
    codeBlock: { bg: '#f8f9fa', border: '#e9ecef' },
    inlineCode: { bg: '#f1f3f4', color: '#e91e63' },
    blockquote: { border: '#3498db', bg: '#f8fafb', color: '#555' },
    link: { color: '#3498db' },
  },
  warm: {
    id: 'warm',
    name: '暖橙',
    description: '温暖橙色系，适合生活/人文类内容',
    h1: { color: '#c0392b', border: '#e67e22' },
    h2: { color: '#e67e22', prefix: '🔥' },
    h3: { color: '#d35400', prefix: '▸' },
    h4: { color: '#a0522d', prefix: '·' },
    bold: { color: '#c0392b' },
    italic: { color: '#d35400' },
    del: { color: '#bdc3c7' },
    codeBlock: { bg: '#fef9f4', border: '#f5d9b5' },
    inlineCode: { bg: '#fdf2e9', color: '#c0392b' },
    blockquote: { border: '#e67e22', bg: '#fef9f4', color: '#7f5539' },
    link: { color: '#e67e22' },
  },
  minimal: {
    id: 'minimal',
    name: '极简黑白',
    description: '黑白灰极简风，适合技术/严肃内容',
    h1: { color: '#1a1a1a', border: '#333' },
    h2: { color: '#333', prefix: '#' },
    h3: { color: '#555', prefix: '##' },
    h4: { color: '#777', prefix: '###' },
    bold: { color: '#1a1a1a' },
    italic: { color: '#666' },
    del: { color: '#aaa' },
    codeBlock: { bg: '#f5f5f5', border: '#ddd' },
    inlineCode: { bg: '#f0f0f0', color: '#333' },
    blockquote: { border: '#999', bg: '#f9f9f9', color: '#666' },
    link: { color: '#333' },
  },
  green: {
    id: 'green',
    name: '清新绿',
    description: '绿色自然系，适合健康/科普/环保内容',
    h1: { color: '#1e5631', border: '#27ae60' },
    h2: { color: '#27ae60', prefix: '🌿' },
    h3: { color: '#2ecc71', prefix: '▹' },
    h4: { color: '#16a085', prefix: '◦' },
    bold: { color: '#27ae60' },
    italic: { color: '#2ecc71' },
    del: { color: '#a0c4a8' },
    codeBlock: { bg: '#f0faf3', border: '#c8e6c9' },
    inlineCode: { bg: '#e8f5e9', color: '#1e5631' },
    blockquote: { border: '#27ae60', bg: '#f0faf3', color: '#2e7d32' },
    link: { color: '#27ae60' },
  },
};

/** 列出所有可用主题 */
export function listThemes(): { id: string; name: string; description: string }[] {
  return Object.values(THEMES).map(t => ({
    id: t.id,
    name: t.name,
    description: t.description,
  }));
}

/** 获取单个主题配置 */
export function getTheme(id: string): ThemeConfig | undefined {
  return THEMES[id];
}

/** HTML 实体转义 */
function escapeHtml(text: string): string {
  const map: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;',
  };
  return text.replace(/[&<>"']/g, m => map[m]);
}

/** 主入口：Markdown → 微信兼容 HTML */
export function formatWeChatArticle(markdown: string, options: FormatOptions = {}): string {
  if (!markdown || typeof markdown !== 'string') return '';

  const theme = THEMES[options.theme || 'default'] || THEMES.default;
  const fontSize = options.fontSize || 16;
  const lineHeight = options.lineHeight || 1.8;

  let html = markdown;

  // 1. 代码块（最先处理，避免被后续规则影响）
  html = convertCodeBlocks(html, theme);

  // 2. 标题
  html = convertHeadings(html, theme);

  // 3. 文本格式
  html = convertTextFormatting(html, theme);

  // 4. 列表
  html = convertLists(html);

  // 5. 引用
  html = convertBlockquotes(html, theme);

  // 6. 链接
  html = convertLinks(html, theme);

  // 7. 表格
  html = convertTables(html);

  // 8. 段落
  html = convertParagraphs(html, fontSize, lineHeight);

  // 9. 清理
  html = cleanupHTML(html);

  // 10. 添加基础样式
  return addBaseStyles(html, fontSize, lineHeight);
}

function convertCodeBlocks(html: string, theme: ThemeConfig): string {
  // 围栏代码块
  html = html.replace(
    /```(\w+)?\n([\s\S]*?)```/g,
    (_match, lang, code) => {
      const language = lang || 'text';
      return `<pre data-language="${language}" style="background: ${theme.codeBlock.bg}; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 14px; line-height: 1.4; border: 1px solid ${theme.codeBlock.border}; margin: 16px 0;"><code style="color: #333; background: none; padding: 0;">${escapeHtml(code.trim())}</code></pre>`;
    }
  );

  // 行内代码
  html = html.replace(
    /`([^`]+)`/g,
    `<code style="background: ${theme.inlineCode.bg}; color: ${theme.inlineCode.color}; padding: 2px 6px; border-radius: 4px; font-size: 0.9em;">$1</code>`
  );

  return html;
}

function convertHeadings(html: string, theme: ThemeConfig): string {
  html = html.replace(
    /^# (.+)$/gm,
    `<h1 style="color: ${theme.h1.color}; font-size: 26px; font-weight: bold; margin: 24px 0 16px 0; line-height: 1.3; border-bottom: 3px solid ${theme.h1.border}; padding-bottom: 8px;">$1</h1>`
  );
  html = html.replace(
    /^## (.+)$/gm,
    `<h2 style="color: ${theme.h2.color}; font-size: 22px; font-weight: bold; margin: 20px 0 12px 0; line-height: 1.3;">${theme.h2.prefix} $1</h2>`
  );
  html = html.replace(
    /^### (.+)$/gm,
    `<h3 style="color: ${theme.h3.color}; font-size: 18px; font-weight: bold; margin: 18px 0 10px 0; line-height: 1.3;">${theme.h3.prefix} $1</h3>`
  );
  html = html.replace(
    /^#### (.+)$/gm,
    `<h4 style="color: ${theme.h4.color}; font-size: 17px; font-weight: bold; margin: 16px 0 8px 0; line-height: 1.3;">${theme.h4.prefix} $1</h4>`
  );
  return html;
}

function convertTextFormatting(html: string, theme: ThemeConfig): string {
  html = html.replace(
    /\*\*(.+?)\*\*/g,
    `<strong style="color: ${theme.bold.color}; font-weight: bold;">$1</strong>`
  );
  html = html.replace(
    /\*(?!\*)(.+?)(?<!\*)\*/g,
    `<em style="color: ${theme.italic.color}; font-style: italic;">$1</em>`
  );
  html = html.replace(
    /~~(.+?)~~/g,
    `<del style="color: ${theme.del.color}; text-decoration: line-through;">$1</del>`
  );
  return html;
}

function convertLists(html: string): string {
  // 有序列表
  html = html.replace(
    /^\d+\.\s+(.+)$/gm,
    '<li style="margin: 8px 0; line-height: 1.6;">$1</li>'
  );
  // 无序列表
  html = html.replace(
    /^[-*+]\s+(.+)$/gm,
    '<li style="margin: 8px 0; line-height: 1.6;">$1</li>'
  );
  // 包裹连续的 <li>
  html = html.replace(
    /(<li[^>]*>.*?<\/li>(\s*<li[^>]*>.*?<\/li>)*)/gs,
    (match: string) => `<ul style="margin: 16px 0; padding-left: 24px; list-style-type: disc;">${match}</ul>`
  );
  return html;
}

function convertBlockquotes(html: string, theme: ThemeConfig): string {
  return html.replace(
    /^>\s*(.+)$/gm,
    `<blockquote style="border-left: 4px solid ${theme.blockquote.border}; padding: 16px 20px; margin: 16px 0; background: ${theme.blockquote.bg}; font-style: italic; color: ${theme.blockquote.color}; border-radius: 0 8px 8px 0;">$1</blockquote>`
  );
}

function convertLinks(html: string, theme: ThemeConfig): string {
  return html.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    `<a href="$2" style="color: ${theme.link.color}; text-decoration: none; border-bottom: 1px dotted ${theme.link.color};" target="_blank">$1</a>`
  );
}

function convertTables(html: string): string {
  const lines = html.split('\n');
  let inTable = false;
  let tableLines: string[] = [];
  const result: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.includes('|') && !inTable) {
      inTable = true;
      tableLines = [trimmed];
    } else if (trimmed.includes('|') && inTable) {
      tableLines.push(trimmed);
    } else if (inTable) {
      if (tableLines.length > 0) result.push(convertTableToHTML(tableLines));
      inTable = false;
      tableLines = [];
      result.push(line);
    } else {
      result.push(line);
    }
  }
  if (inTable && tableLines.length > 0) {
    result.push(convertTableToHTML(tableLines));
  }
  return result.join('\n');
}

function convertTableToHTML(tableLines: string[]): string {
  if (tableLines.length < 2) return tableLines.join('\n');

  const headerLine = tableLines[0];
  const separatorLine = tableLines[1];
  const dataLines = tableLines.slice(2);

  const headers = headerLine.split('|').map(h => h.trim()).filter(h => h);
  if (!separatorLine.includes('-')) return tableLines.join('\n');

  let tableHTML =
    '<table style="width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">';
  tableHTML += '<thead><tr style="background: #f8f9fa;">';
  headers.forEach(header => {
    tableHTML += `<th style="border: 1px solid #dee2e6; padding: 12px 8px; text-align: left; font-weight: bold; color: #495057;">${header}</th>`;
  });
  tableHTML += '</tr></thead><tbody>';
  dataLines.forEach((line, index) => {
    const cells = line.split('|').map(c => c.trim()).filter(c => c);
    const bg = index % 2 === 0 ? '#ffffff' : '#f8f9fa';
    tableHTML += `<tr style="background: ${bg};">`;
    cells.forEach(cell => {
      tableHTML += `<th style="border: 1px solid #dee2e6; padding: 12px 8px; color: #495057;">${cell}</th>`;
    });
    tableHTML += '</tr>';
  });
  tableHTML += '</tbody></table>';
  return tableHTML;
}

function convertParagraphs(html: string, fontSize: number, lineHeight: number): string {
  html = html.replace(
    /\n\s*\n/g,
    `</p><p style="margin: 15px 0; line-height: ${lineHeight}; text-align: justify; color: #333; font-size: ${fontSize}px;">`
  );
  html =
    `<p style="margin: 15px 0; line-height: ${lineHeight}; text-align: justify; color: #333; font-size: ${fontSize}px;">${html}</p>`;
  return html;
}

function cleanupHTML(html: string): string {
  html = html.replace(/<p[^>]*>\s*<\/p>/g, '');
  html = html.replace(/<p[^>]*>(\s*<h[1-6][^>]*>.*?<\/h[1-6]>\s*)<\/p>/g, '$1');
  html = html.replace(/<p[^>]*>(\s*<ul[^>]*>.*?<\/ul>\s*)<\/p>/gs, '$1');
  html = html.replace(/<p[^>]*>(\s*<ol[^>]*>.*?<\/ol>\s*)<\/p>/gs, '$1');
  html = html.replace(/<p[^>]*>(\s*<blockquote[^>]*>.*?<\/blockquote>\s*)<\/p>/gs, '$1');
  html = html.replace(/<p[^>]*>(\s*<pre[^>]*>.*?<\/pre>\s*)<\/p>/gs, '$1');
  html = html.replace(/<p[^>]*>(\s*<table[^>]*>.*?<\/table>\s*)<\/p>/gs, '$1');
  return html;
}

function addBaseStyles(html: string, fontSize: number, lineHeight: number): string {
  const style = `
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Roboto", "Helvetica Neue", Arial, sans-serif;
    line-height: ${lineHeight};
    color: #333;
    background: #fff;
    font-size: ${fontSize}px;
    margin: 0;
    padding: 20px;
  }
  img { max-width: 100%; height: auto; display: block; margin: 16px auto; border-radius: 8px; }
  hr { border: none; height: 1px; background: linear-gradient(to right, transparent, #ddd, transparent); margin: 24px 0; }
</style>
`;
  return style + html;
}
