# skills
My Agent skills and testing playground

## Skills
- [`plan-board`](plugins/plan-board/skills/plan-board/SKILL.md): turn an agreed implementation plan into a visual HTML board plus a YAML hand-off file.

## Install
### In a repository, via `settings.json`
Add this to `.claude/settings.json` in your repo and commit it. Anyone who opens the repo in Claude Code is asked to trust the folder, then prompted to install the marketplace and plugin.
```json
{
  "extraKnownMarketplaces": {
    "fefi95-skills": {
      "source": { "source": "github", "repo": "fefi95/skills" }
    }
  },
  "enabledPlugins": {
    "plan-board@fefi95-skills": true
  }
}
```
Use `.claude/settings.local.json` instead if you only want it for yourself (Claude Code keeps it out of git), or `~/.claude/settings.json` to have it in every repo.

### With commands
In Claude Code:
```
/plugin marketplace add fefi95/skills
/plugin install plan-board@fefi95-skills
```
Then run `/plan-board:plan-board` (or just ask for a plan board).
