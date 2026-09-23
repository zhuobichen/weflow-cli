/**
 * 显示形态里的小工具。
 *
 * 只放跨模块共用的：`clipWithMarker` 有两处需要——AppMsg 的显示形态
 * （`core/appMsgFormat.ts`，引用消息的正文与被引原文各截一次）和助手读聊天记录时
 * 每条消息的字数上限（`services/assistantTools.ts`）。
 *
 * Python 侧有一份同义的实现（`nt_decrypt.py` 的 `_clip`），做的是同一件事。
 */

/**
 * 超长截断并**留下省略号**。
 *
 * 不标记的截断读起来像一句说完的话：被引原文最长的 12733 字（引用了一整篇公众号文章），
 * 切完看着像"这就是全部"；助手把一条 300 字的聊天记录切到 160，模型会以为对方就说完了
 * 这半句。标记只花一个字符，换来的是"这里被切过"这个事实没有丢。
 */
export function clipWithMarker(text: string, limit: number): string {
  return text.length <= limit ? text : text.slice(0, limit) + '…'
}
