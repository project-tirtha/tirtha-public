# Contributing to Project Tirtha

Thank you for your interest in contributing to Project Tirtha. We welcome contributors from diverse backgrounds including students, researchers, developers, heritage professionals, and members of the public. Whether you are here to fix a bug, add features, improve documentation, or propose a research idea, you are welcome.

### Table of Contents
- [Ways to Contribute](#ways-to-contribute)
- [LLM / AI Tool Usage Policy](#llm--ai-tool-usage-policy)
- [Communication Guidelines](#communication-guidelines)
- [Contributing as a Coder](#contributing-as-a-coder)
  - [Before You Start](#before-you-start)
  - [Development Workflow](#development-workflow)
- ⭐ [Google Summer of Code (GSoC) 2026](#google-summer-of-code-gsoc-2026)
- [Contributing as a Non-Coder](#contributing-as-a-non-coder)
  - [If you want to contribute data for a heritage site](#if-you-want-to-contribute-data-for-a-heritage-site)
  - [If you are a Researcher](#if-you-are-a-researcher)
- [Attribution and Licensing](#attribution-and-licensing)


## Ways to Contribute

You can help Project Tirtha in many ways:

- [As a coder](#contributing-as-a-coder):
  - backend pipelines, frontend UI/UX, Bug reports, and fixes
  - research ideas, RFCs, and design discussions
- [As a Non-coder](#contributing-as-a-non-coder):
  - dataset curation and metadata improvements
  - outreach, community building, and partnerships

No contribution is too small.

Before you start, please read the relevant sections below, specifically the [LLM / AI Tool Usage Policy](#llm--ai-tool-usage-policy) and [Communication](#communication) guidelines.

---

## LLM / AI Tool Usage Policy

We allow and encourage the use of LLMs and AI tools as assistive aids for:

- Drafting code and prototypes
- Writing issues, PR descriptions, and comments
- Drafting RFCs and design documents
- Improving clarity of documentation
- Brainstorming GSoC proposals

However:

- You are fully responsible for the correctness, safety, and licensing of anything you submit.
- Do not paste proprietary, confidential, or non-redistributable material into LLMs.
- Ensure you understand and can explain any AI-generated code you contribute.
- For substantial AI-assisted content, note this briefly but explicitly in your PR or proposal.
- GSoC proposals must reflect your technical understanding and design thinking. LLMs may help with phrasing, but cannot replace ownership.


## Communication Guidelines

- Use [GitHub Issues](https://github.com/project-tirtha/tirtha-public/issues) for reporting bugs and actionable work.
- Use [GitHub Discussions](https://github.com/orgs/project-tirtha/discussions) for open-ended questions and ideas.
- Use the [Matrix room](https://matrix.to/#/#tirtha:matrix.org) for quick conversations and community interaction.

For community norms and behavior expectations, see [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md). Bottom line: Be patient and kind. Contributors span time zones, cultures, and experience levels. 

---

## Contributing as a Coder

### Before You Start

1. Read the repository [README](./README.md) to understand the project.
2. Browse the [Tirtha website](https://smlab.niser.ac.in/project/tirtha/) to understand the project from a user's perspective.
3. Skim [DEVELOP.md](./DEVELOP.md) for local development setup.
4. Browse open issues, especially those labeled [`good first issue`](https://github.com/project-tirtha/tirtha-public/issues?q=is%3Aissue%20state%3Aopen%20label%3A%22good%20first%20issue%22).
5. Join the community via [Matrix](https://matrix.to/#/#tirtha:matrix.org) or [GitHub Discussions](https://github.com/orgs/project-tirtha/discussions).

If anything is unclear, open a [GitHub Discussion](https://github.com/orgs/project-tirtha/discussions) or ask in the [chat room](https://matrix.to/#/#tirtha:matrix.org).

### Development Workflow

> [!tip]
> You do not need a GPU for UI or documentation changes. GPU and CUDA are required for pipeline work.

For setup, please see [DEVELOP.md](./DEVELOP.md). If you are only working on the frontend (`Django` templates or `HTML/CSS/JS`), you may skip installing heavy backend dependencies; check [`DEVELOP.md`](./DEVELOP.md) for the `start-dev.sh` workflow.

1. [Fork](https://github.com/project-tirtha/tirtha-public/fork) the repository.
2. Create a branch:
   ```bash
   git checkout -b <yourname>/<feature-short-desc>
   ```
3. Make focused, logical changes — one logical change per PR is preferred.
4. Commit with clear messages:
   ```bash
   git commit -m "(feat) add XYZ pipeline stage to Tirtha backend"
   ```
5. Ensure you sync with the latest `main` branch:
   ```bash
   git fetch origin
   git checkout main
   git pull origin main
   git checkout <your-branch>
   git merge main
   ```
6. Push code and open a [Pull Request (PR)](https://github.com/project-tirtha/tirtha-public/pulls).  Draft PRs are welcome and encouraged for early feedback.


**Pull Requests**

A good PR:

- Has a clear purpose.
- References a related issue if any.
- Explains what changed and why.
- Includes screenshots for UI changes.
- Includes benchmarks or notes for pipeline changes.

Maintainers may request changes. This is a normal part of FOSS collaboration.

**Issues and Bug Reports**

Use GitHub Issues for:

- [Bug reports](https://github.com/project-tirtha/tirtha-public/issues/new/choose)
- [Feature requests](https://github.com/project-tirtha/tirtha-public/issues/new/choose)

When opening an issue:

- Be clear and concise.
- Include logs, screenshots, or minimal reproduction steps if relevant.

---

## Google Summer of Code (GSoC) 2026

> [!important]
> Make sure to read the [LLM / AI Tool Usage](#llm--ai-tool-usage-policy) and [Communication](#communication) sections. Then, read the project ideas document linked in the [README](./README.md) before engaging.

We actively welcome GSoC 2026 contributors.

Guidelines:
<!-- (chore) Create links to both -->
- Join discussions early and introduce yourself (Matrix or GitHub Discussions).
- Make at least one small contribution before submitting a proposal. See issues labeled [`good first issue`](https://github.com/project-tirtha/tirtha-public/issues?q=is%3Aissue%20state%3Aopen%20label%3A%22good%20first%20issue%22) for small contribution ideas.
- Proposals should:
  - Reference concrete parts of the codebase
  - Include milestones and deliverables
  - Identify risks and mitigation strategies
  - Describe how you will engage with the community

We value initiative, clarity, and sustained engagement more than polished prose. Feel free to use this template for your proposal: [GSoC Proposal Template](TBD). We also encourage you to ask for feedback on your proposal drafts via GitHub Discussions or Matrix, long before the submission deadline.

---

## Contributing as a Non-Coder

Use GitHub Discussions for:

- Request for Comment Documents (RFCs) (for design or research proposals)
- **GSoC-related discussions**

For RFCs, describe:
  - The problem
  - Constraints
  - Your proposal
  - Alternatives considered

The same [LLM / AI Tool Usage Policy](#llm--ai-tool-usage-policy) applies.

Open a [GitHub issue](https://github.com/project-tirtha/tirtha-public/issues/new/choose) or come chat with us on [Matrix](https://matrix.to/#/#tirtha:matrix.org) if you run into problems.

### If you want to contribute data for a heritage site

- Upload images and **suggest new sites** via the [Tirtha website](https://smlab.niser.ac.in/project/tirtha/).
- Report issues or request additions through GitHub.
- To run your own Tirtha instance, follow [`README.md`](./README.md) and [`DEVELOP.md`](./DEVELOP.md).

### If you are a Researcher

- Submit images through the [Tirtha website](https://smlab.niser.ac.in/project/tirtha/).
- For research access to the Tirtha database, contact [project.tirtha@niser.ac.in](mailto:project.tirtha@niser.ac.in).
- To run your own Tirtha instance, follow [`README.md`](./README.md) and [`DEVELOP.md`](./DEVELOP.md).

---

## Attribution and Licensing

Project Tirtha's code is licensed under the [GNU AGPL v3.0](./LICENSE), while contributed images and media are licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Outputs generated from contributions are licensed under [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/). By contributing:

- You agree that:
  - your code contributions are licensed under [AGPL v3.0](./LICENSE).
  - your image/media contributions are licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
  - the outputs created from your contributions are licensed under [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/).
- Submit only work you have the right to license.
- Cite external sources where appropriate.

We look forward to building Tirtha together. Thank you for your contributions!
