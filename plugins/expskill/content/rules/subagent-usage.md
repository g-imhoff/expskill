# Subagent usage

Use the orchestration tools directly:

- For child work: 
  - the model you want to launch is accessible through your harness : use the native subagent tool.
  - the model you want to launch is not accessible through your harnes : Call `orchestrator_capabilities` first, then `delegate_task`. Completion of the task wakes you.
- for AI-as-user loop: use `t3_thread_launch` + `t3_thread_send` + `t3_thread_read` / `t3_thread_wait`, launch a top-level thread with `message`, then send follow-ups and read results. That is the conversation pattern.

make sure they always work independently using different worktrees and once the worktree not needed anymore don't forget to delete it.

Follow this cost model graduation for subagent : 
- low cost : `muse spark 1.3 contributor xhigh` on `opencode go`
- medium cost : `sonnet 5.5 xhigh/ultracode` on `claude code`
- high cost : `opus 5.5 xhigh/ultracode` on `claude code`
- frontend : `opus 5.5 xhigh` on `claude code`
