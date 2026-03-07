---
name: acpx
description: Agent Client Protocol (ACP) CLI for orchestrating external AI coding agents. Use acpx to delegate tasks to Codex, Claude Code, Gemini, or other ACP-enabled agents. Supports persistent sessions, parallel workflows, and structured output.
---

# acpx Skill

Use `acpx` to orchestrate external AI coding agents via the Agent Client Protocol (ACP).

## Supported Agents

- `codex` - OpenAI Codex agent
- `claude` - Anthropic Claude Code
- `gemini` - Google Gemini
- `opencode` - OpenCode
- `pi` - Pi agent

## Quick Start

```bash
# Create a new session
acpx codex sessions new

# Send a prompt
acpx codex "fix the tests"

# List sessions
acpx codex sessions
```

## Session Management

```bash
# Create new session (default name is "cwd")
acpx codex sessions new

# Create named session for parallel workflows
acpx codex sessions new --name backend
acpx codex sessions new --name frontend
acpx codex sessions new --name tests

# Use specific session
acpx -s backend "fix the API"

# Close session
acpx codex sessions close backend

# List all sessions
acpx codex sessions
```

## Prompts

```bash
# Simple prompt
acpx codex "fix the login bug"

# Prompt from file
acpx codex --file prompt.md

# Prompt from stdin
echo "refactor auth module" | acpx codex

# Queue prompt without waiting (for follow-up)
acpx codex --no-wait "add unit tests"
```

## Options

| Option | Description |
|--------|-------------|
| `-s, --session <name>` | Use named session |
| `-f, --file <path>` | Read prompt from file |
| `--no-wait` | Queue and return immediately |
| `--approve-all` | Auto-approve all permissions |
| `--approve-reads` | Auto-approve read, prompt for writes |
| `--format json` | JSON output (default: text) |
| `--cwd <dir>` | Working directory |
| `--timeout <sec>` | Max wait time |
| `--verbose` | Debug logs |

## Examples

### Code Review

```bash
acpx codex --format json "review this PR for security issues"
```

### Parallel Tasks

```bash
# Terminal 1: Backend work
acpx -s backend "implement user API"

# Terminal 2: Frontend work  
acpx -s frontend "implement login UI"

# Terminal 3: Tests
acpx -s tests "write integration tests"
```

### Using Claude Code

```bash
acpx claude sessions new
acpx claude "refactor the database layer"
acpx claude --format json "explain this error"
```

### Using Gemini

```bash
acpx gemini sessions new
acpx gemini "add logging to all functions"
```

## Permission Modes

| Mode | Description |
|------|-------------|
| `--approve-all` | Everything auto-approved |
| `--approve-reads` | Reads auto-approved, writes prompt |
| `--deny-all` | Deny all permission requests |

## Output Formats

```bash
# Plain text (default)
acpx codex "what does this do"

# JSON for programmatic use
acpx --format json codex "list all bugs"

# Quiet (no output)
acpx --format quiet codex "run tests"
```

## Tips

- Use named sessions for parallel workflows
- Use `--format json` for automation
- Use `--no-wait` to queue follow-up tasks
- Sessions persist across calls (stored in `~/.acpx/sessions/`)
- Use `--approve-all` for CI/CD pipelines
