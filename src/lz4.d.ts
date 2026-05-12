declare module 'lz4' {
  export function decodeBlock(
    input: Buffer,
    output?: Buffer,
    sIdx?: number,
    eIdx?: number
  ): number

  export function decodeBound(size: number): number
}
