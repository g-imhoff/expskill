# ExpSkill

Skills and agents for Codex, OpenCode, and Hermes. The installer asks which host to use.
Requires Bash and authenticated GitHub CLI (`gh auth login`) with access to this private repository, plus your chosen host CLI.
Codex and Hermes also need Git and GitHub SSH access; Codex needs Python 3.
```bash
installer="$(gh api -H 'Accept: application/vnd.github.raw+json' 'repos/g-imhoff/expskill/contents/install.sh?ref=main')" && bash -c "$installer"
```
[Usage, manual installation, and development guide](docs/guide.md)
