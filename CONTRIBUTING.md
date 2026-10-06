# Contributing

```bash
git clone https://github.com/engels-florentino/SocialCli.git
cd SocialCli
uv sync --group dev
uv run pytest
uv run socialcli --help
```

Open an issue with expected behavior, observed results and reproducible steps without credentials. Use fictional brands and simulated HTTP responses in tests. Do not access real accounts from CI or include private operational data.

Publishing changes must preserve previews, approval, brand isolation and handling of ambiguous results. Never mark a submission awaiting confirmation as published. Provider features must describe actual permissions and limits without assuming application approval.

The Python module remains `socialctl` for compatibility; the distribution and primary command are `socialcli`. Submit small PRs and explain what changed and how you verified it. Contributions are distributed under the project's MIT license.
