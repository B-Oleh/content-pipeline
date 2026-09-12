# Autonomous Agent Policy

Agents may autonomously:

- read repository files
- edit repository files
- create repository files
- run tests
- run linters
- run ffmpeg and local renders
- inspect git status, diff, and log
- diagnose failures
- fix code
- retry failed tests
- inspect existing project documentation

Human approval is required before:

- force push
- rewriting git history
- deleting remote branches
- deleting remote data
- modifying GitHub secrets
- modifying OAuth credentials
- enabling paid services or billing
- publicly publishing content
- changing account security settings
- destructive actions outside the repository

Do not expose, print, commit, or copy secrets.

Do not modify unrelated functionality.

The autonomous loop may create local changes but must not push until the workflow explicitly allows it.
