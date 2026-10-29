---
name: setup-design
description: Set up, repair or update a configuration specific to a repository inside of `./expskill/design` helping to sketch and follow project design guidelines.
---

//TODO : steal font form ui-ux pro max ? actually would certainly fit better inside of setup-design
## Precondition

- Run this in an existing project or when starting a new one.
- If a `.gitignore` exists, make sure to keep `./expskill/design` tracked and committed.

## Sketching and preview

Review is faster when the user and the team can open the UI from a link and comment. The agent needs a workspace to sketch in the project's own stack and share that sketch by link.

If the project builds to static HTML/CSS/JS, suggest [yodea](https://github.com/g-imhoff/yodea).

If not, load `$deep-research` and list the options:

- If preview needs a full project setup, ask the user to continue without design setup for now and tell the user how to unblock it.
- If setup looks trivial, describe it and ask if you should do it.

### Result

Write the chosen solution to `./expskill/design/references/sketch-preview.md` in the repo with:

- Chosen solution
- Install steps
- Short usage guide

## Project guidelines

(ONLY if inside of an existing project) Following `{expskill_folder}/rules/subagent-usage.md` launch 3 to 5 (depending on the existing project size) low-cost subagent that will gather the existing conventions inside of the project.

After that, offer the user a choice: write `./expskill/design/references/design-guidelines.md` following the result section from what you found, or load `$brainstorm` when the project is thin or inconsistent and needs real design work.

### Result

Write the chosen project design guidelines to `./expskill/design/references/design-guidelines.md` in the repo with : 

- Source files or links.
- Colors, type, fonts, spacing, assets.
- Artistic direction: what is the artistic direction of the project.
- Sketches that fit: one layout, one type pairing, one spacing habit to copy
- How to use them in sketches.
- References: few outside links with one sentence each on what to borrow.

## Finish

Create an file inside of `./expskill/design/setup.md` and put inside of it :

```markdown
---
done: true
---
```

Or false if we couldn't finish this setup.
