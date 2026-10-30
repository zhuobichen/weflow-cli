/**
 * 示例插件：一个最小的 MCP server（stdio）。
 *
 * 它证明三件事，写自己的插件时照着改就行：
 *   1. 插件就是**普通的 MCP server**，不需要依赖本仓库的任何代码——用官方 SDK 或者手写
 *      JSON-RPC 都行，宿主那边只按 MCP 协议说话；
 *   2. 进程从 stdin 收 JSON-RPC、往 stdout 写 JSON-RPC，**别往 stdout 打印调试信息**
 *      （那会污染协议流；调试输出走 stderr）；
 *   3. 工具的参数校验归插件自己管——宿主只负责转发，不会替你觉得参数合不合法。
 *
 * 跑起来看：weflow-cli plugin add examples/plugins/hello && weflow-cli plugin doctor
 */
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js'

const server = new Server(
  { name: 'weflow-plugin-hello', version: '0.1.0' },
  { capabilities: { tools: {} } },
)

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'echo',
      description: '把收到的文本原样返回。用来确认插件链路是否打通。',
      inputSchema: {
        type: 'object',
        properties: {
          text: { type: 'string', description: '想回显的文本' },
        },
        required: ['text'],
      },
    },
  ],
}))

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args = {} } = request.params
  if (name !== 'echo') {
    return { content: [{ type: 'text', text: `未知工具：${name}` }], isError: true }
  }
  const text = typeof args.text === 'string' ? args.text : ''
  if (!text) {
    return { content: [{ type: 'text', text: 'text 不能为空' }], isError: true }
  }
  return { content: [{ type: 'text', text: `回声：${text}` }] }
})

await server.connect(new StdioServerTransport())
