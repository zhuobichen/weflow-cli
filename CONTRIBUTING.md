# Contributing to WeFlow CLI

Thanks for improving WeFlow CLI. This project handles local personal data, so correctness, privacy and reproducibility matter more than broad refactors.

## Before opening an issue

Include the command you ran, the operating system, Node.js and Python versions, the WeChat version, and a minimal reproduction. Remove names, wxid values, database paths, API keys, tokens, chat content and screenshots containing private data.

For security-sensitive reports, use the process in [SECURITY.md](./SECURITY.md) instead of a public issue.

## Development setup

```powershell
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install
python -m pip install -r requirements.txt
npm run build
```

Use `npm run dev -- <command>` while developing. Run `npm run build` before opening a pull request.

## Pull request guidelines

- Keep pull requests focused on one user-visible change or repair.
- Do not commit `.env`, `.mcp.json`, `output/`, databases, decrypted data, keys, tokens, chat exports or screenshots containing personal data.
- Update README, setup instructions or command help when a workflow changes.
- Explain the validation performed and any platform limitation in the pull request description.
- Preserve Windows compatibility unless the change explicitly documents a supported-platform adjustment.

## Documentation changes

Keep documentation responsibilities separate:

- `README.md`: product overview and shortest path to first use.
- `docs/SETUP.md`: a complete fresh-machine installation.
- `OPERATIONS.md`: maintenance and deep troubleshooting.
- `docs/MCP.md`: MCP client integration and tool boundaries.
