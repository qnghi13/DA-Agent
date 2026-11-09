# Third-party notices

DA-Agent is licensed under Apache-2.0 (`LICENSE`). It includes, or its history once included, the
following third-party material. Dependencies installed by `uv` and `npm` keep their own licenses
in their packages and are not listed here.

## In the source tree

| Material | Where | Source | License |
|---|---|---|---|
| shadcn/ui components, copied and edited | `web/src/components/ui/` | [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | MIT, Copyright (c) 2023 shadcn |
| Geist and Geist Mono fonts, bundled into the web build | `@fontsource-variable/geist*` | [vercel/geist-font](https://github.com/vercel/geist-font) | SIL Open Font License 1.1 |

## In earlier commits only

These agent skills were committed before commit `354a2d3` and are no longer tracked. Contributors
restore them with `skills-lock.json` (see the README, Development).

| Skill | Source | License |
|---|---|---|
| `brainstorming` | [obra/superpowers](https://github.com/obra/superpowers) | MIT, Copyright (c) 2025 Jesse Vincent |
| `find-skills` | [vercel-labs/skills](https://github.com/vercel-labs/skills) | MIT, Copyright (c) 2026 Vercel, Inc. |
| `agent-browser` | [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) | Apache-2.0 |
| `frontend-design` | [anthropics/skills](https://github.com/anthropics/skills) | Apache-2.0 (its `LICENSE.txt` was committed with it) |
| `playwright-cli` | [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) | Apache-2.0 |

The Apache-2.0 text is in `LICENSE`. None of the Apache-2.0 sources above has a NOTICE file.

## MIT License

The MIT-licensed material above is provided under these terms, with the copyright lines given in
the tables:

```text
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
