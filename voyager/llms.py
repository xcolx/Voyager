import json
import shutil
import subprocess
import tempfile
import time

from langchain.schema import AIMessage, BaseMessage, HumanMessage, SystemMessage

# map the original OpenAI defaults to Claude CLI model aliases
CLAUDE_MODEL_ALIASES = {
    "gpt-4": "sonnet",
    "gpt-3.5-turbo": "haiku",
}


class ClaudeCLIChat:
    """
    Drop-in replacement for langchain's ChatOpenAI that shells out to the local `claude` CLI.
    Each call is a single stateless completion: tools, MCP servers and session persistence are disabled.
    Usage matches how Voyager calls the LLM: `llm(messages).content`.
    """

    total_usage = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    }

    def __init__(
        self,
        model_name="sonnet",
        temperature=0,
        request_timeout=240,
        max_retries=3,
        cli_path="claude",
    ):
        self.model_name = CLAUDE_MODEL_ALIASES.get(model_name, model_name)
        # the CLI does not expose temperature, kept only for signature compatibility
        self.temperature = temperature
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.cli_path = shutil.which(cli_path) or cli_path
        # run outside the repo so no CLAUDE.md or project settings leak into the prompt
        self.cwd = tempfile.mkdtemp(prefix="voyager_claude_")

    def __call__(self, messages: list[BaseMessage]) -> AIMessage:
        system_prompt = "\n\n".join(
            m.content for m in messages if isinstance(m, SystemMessage)
        )
        prompt = "\n\n".join(
            m.content for m in messages if not isinstance(m, SystemMessage)
        )
        cmd = [
            self.cli_path,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model_name,
            "--tools",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]

        last_error = None
        for i in range(self.max_retries):
            try:
                # prompt goes through stdin to avoid argv length limits
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.request_timeout,
                    cwd=self.cwd,
                )
                result = json.loads(proc.stdout)
                if result.get("is_error"):
                    raise RuntimeError(result.get("result"))
                self._log_usage(result.get("usage", {}))
                return AIMessage(content=result["result"])
            except (
                subprocess.TimeoutExpired,
                json.JSONDecodeError,
                RuntimeError,
                KeyError,
            ) as e:
                last_error = e
                print(
                    f"\033[31mClaude CLI call failed ({i + 1}/{self.max_retries}): {e}\033[0m"
                )
                time.sleep(2**i)
        raise RuntimeError(f"Claude CLI failed after {self.max_retries} tries: {last_error}")

    def _log_usage(self, usage):
        # running totals across all agents, to keep an eye on token spend
        for key in ClaudeCLIChat.total_usage:
            ClaudeCLIChat.total_usage[key] += usage.get(key, 0)
        total = ClaudeCLIChat.total_usage
        print(
            f"\033[90mClaude {self.model_name}: in {usage.get('input_tokens', 0)}"
            f" (cache read {usage.get('cache_read_input_tokens', 0)}), out {usage.get('output_tokens', 0)}"
            f" | total in {total['input_tokens'] + total['cache_creation_input_tokens']}"
            f" (cache read {total['cache_read_input_tokens']}), out {total['output_tokens']}\033[0m"
        )


def make_chat_model(backend, model_name, temperature, request_timeout):
    if backend == "openai":
        from langchain.chat_models import ChatOpenAI

        return ChatOpenAI(
            model_name=model_name,
            temperature=temperature,
            request_timeout=request_timeout,
        )
    if backend == "claude_cli":
        return ClaudeCLIChat(
            model_name=model_name,
            temperature=temperature,
            request_timeout=request_timeout,
        )
    raise ValueError(f"llm backend {backend} not supported")


def make_embeddings(backend):
    if backend == "openai":
        from langchain.embeddings.openai import OpenAIEmbeddings

        return OpenAIEmbeddings()
    if backend == "claude_cli":
        # Claude has no embeddings endpoint, use a local sentence-transformers model
        from langchain.embeddings import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    raise ValueError(f"llm backend {backend} not supported")
