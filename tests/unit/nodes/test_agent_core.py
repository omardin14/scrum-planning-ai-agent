"""Tests for core agent node functions (call_model, should_continue, human_review)."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END

from scrum_agent.agent.nodes import (
    _HIGH_RISK_TOOLS,
    _user_confirmed,
    call_model,
    human_review,
    make_call_model,
    should_continue,
)
from scrum_agent.prompts import get_system_prompt

# ── Core behaviour ───────────────────────────────────────────────────


class TestCallModel:
    """Tests for the call_model() node function."""

    def test_returns_dict_with_messages_key(self, monkeypatch):
        """call_model must return a dict containing a 'messages' key."""
        fake_response = AIMessage(content="Hello!")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        state = {"messages": [HumanMessage(content="Hi")]}
        result = call_model(state)
        assert "messages" in result

    def test_returns_single_item_list(self, monkeypatch):
        """The messages value should be a list with exactly one response."""
        fake_response = AIMessage(content="I can help with that.")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        state = {"messages": [HumanMessage(content="Hello")]}
        result = call_model(state)
        assert len(result["messages"]) == 1

    def test_response_is_ai_message(self, monkeypatch):
        """The returned message should be the AIMessage from the LLM."""
        fake_response = AIMessage(content="Sure, let me help.")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        state = {"messages": [HumanMessage(content="Hello")]}
        result = call_model(state)
        assert result["messages"][0] is fake_response
        assert isinstance(result["messages"][0], AIMessage)

    def test_system_prompt_prepended(self, monkeypatch):
        """The system prompt must be the first message sent to the LLM."""
        fake_response = AIMessage(content="Acknowledged.")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        state = {"messages": [HumanMessage(content="Plan my project")]}
        call_model(state)

        # Extract the message list passed to invoke()
        call_args = mock_llm.invoke.call_args[0][0]
        assert isinstance(call_args[0], SystemMessage)
        assert call_args[0].content == get_system_prompt()

    def test_user_messages_forwarded(self, monkeypatch):
        """User messages from state must be passed through to the LLM."""
        fake_response = AIMessage(content="Got it.")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        user_msg = HumanMessage(content="Build a todo app")
        state = {"messages": [user_msg]}
        call_model(state)

        call_args = mock_llm.invoke.call_args[0][0]
        # System prompt first, then the user message
        assert call_args[1] is user_msg

    def test_multiple_messages_forwarded(self, monkeypatch):
        """All messages in state should be forwarded after the system prompt."""
        fake_response = AIMessage(content="Understood.")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        msg1 = HumanMessage(content="Build a todo app")
        msg2 = AIMessage(content="Tell me more about the project.")
        msg3 = HumanMessage(content="It should have CRUD operations")
        state = {"messages": [msg1, msg2, msg3]}
        call_model(state)

        call_args = mock_llm.invoke.call_args[0][0]
        # System prompt + 3 conversation messages = 4 total
        assert len(call_args) == 4
        assert isinstance(call_args[0], SystemMessage)
        assert call_args[1] is msg1
        assert call_args[2] is msg2
        assert call_args[3] is msg3

    def test_system_prompt_not_in_returned_messages(self, monkeypatch):
        """The system prompt is injected for the LLM call but NOT returned in state."""
        fake_response = AIMessage(content="Here's a plan.")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        state = {"messages": [HumanMessage(content="Hello")]}
        result = call_model(state)

        # Only the AI response is returned — no SystemMessage
        for msg in result["messages"]:
            assert not isinstance(msg, SystemMessage)


# ── Import tests ─────────────────────────────────────────────────────


class TestCallModelImports:
    """Verify call_model is importable from the expected locations."""

    def test_importable_from_agent_package(self):
        """call_model should be re-exported from scrum_agent.agent."""
        from scrum_agent.agent import call_model as imported_fn

        assert imported_fn is call_model

    def test_importable_from_nodes_module(self):
        """call_model should be importable directly from scrum_agent.agent.nodes."""
        from scrum_agent.agent.nodes import call_model as imported_fn

        assert imported_fn is call_model


# ── should_continue routing function ────────────────────────────────


class TestShouldContinue:
    """Tests for the should_continue() conditional edge function."""

    def test_returns_end_when_no_tool_calls(self):
        """Plain AIMessage with no tool_calls should route to END."""
        state = {"messages": [AIMessage(content="Here's your plan.")]}
        assert should_continue(state) == END

    def test_returns_tools_when_tool_calls_present(self):
        """AIMessage with tool_calls should route to 'tools'."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "read_codebase", "args": {}, "id": "call_123"}],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "tools"

    def test_only_inspects_last_message(self):
        """Only the last message matters — earlier messages are ignored."""
        # First message has tool calls, but the last one doesn't
        msg_with_tools = AIMessage(
            content="",
            tool_calls=[{"name": "read_codebase", "args": {}, "id": "call_001"}],
        )
        msg_without_tools = AIMessage(content="Done!")
        state = {"messages": [msg_with_tools, msg_without_tools]}
        assert should_continue(state) == END

    def test_returns_end_when_tool_calls_empty_list(self):
        """An empty tool_calls list should route to END (same as no tool calls)."""
        msg = AIMessage(content="All done.", tool_calls=[])
        state = {"messages": [msg]}
        assert should_continue(state) == END

    def test_multiple_tool_calls_returns_tools(self):
        """Multiple tool calls in one message should still route to 'tools'."""
        msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "read_codebase", "args": {}, "id": "call_001"},
                {"name": "export_markdown", "args": {"path": "plan.md"}, "id": "call_002"},
            ],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "tools"

    def test_does_not_mutate_state(self):
        """should_continue is a pure function — it must not modify the state."""
        original_msg = AIMessage(content="Hello!")
        state = {"messages": [HumanMessage(content="Hi"), original_msg]}
        original_messages = list(state["messages"])

        should_continue(state)

        assert state["messages"] == original_messages


# ── should_continue import tests ────────────────────────────────────


class TestShouldContinueImports:
    """Verify should_continue is importable from the expected locations."""

    def test_importable_from_agent_package(self):
        """should_continue should be re-exported from scrum_agent.agent."""
        from scrum_agent.agent import should_continue as imported_fn

        assert imported_fn is should_continue

    def test_importable_from_nodes_module(self):
        """should_continue should be importable directly from scrum_agent.agent.nodes."""
        from scrum_agent.agent.nodes import should_continue as imported_fn

        assert imported_fn is should_continue


# ── Risk-level routing ────────────────────────────────────────────────


class TestShouldContinueRiskRouting:
    """Tests for the three-way risk-level routing in should_continue."""

    def test_low_risk_tool_routes_to_tools(self):
        """read_codebase is not high-risk — routes directly to 'tools'."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "read_codebase", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "tools"

    def test_github_read_routes_to_tools(self):
        """github_read_repo is low-risk — auto-executes."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "github_read_repo", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "tools"

    def test_jira_create_epic_routes_to_human_review(self):
        """jira_create_epic is high-risk — routes to human_review for confirmation."""
        msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "jira_create_epic", "args": {"title": "Auth Feature"}, "id": "call_1", "type": "tool_call"}
            ],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "human_review"

    def test_jira_create_story_routes_to_human_review(self):
        """jira_create_story is high-risk."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "jira_create_story", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "human_review"

    def test_jira_create_sprint_routes_to_human_review(self):
        """jira_create_sprint is high-risk."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "jira_create_sprint", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "human_review"

    def test_confluence_create_page_routes_to_human_review(self):
        """confluence_create_page is high-risk."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "confluence_create_page", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "human_review"

    def test_confluence_update_page_routes_to_human_review(self):
        """confluence_update_page is high-risk."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "confluence_update_page", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        state = {"messages": [msg]}
        assert should_continue(state) == "human_review"

    def test_confirmed_high_risk_routes_to_tools(self):
        """After user confirms, high-risk tool routes to 'tools' (not human_review again)."""
        confirmation_msg = AIMessage(content="I'd like to create a feature. Please confirm — yes/no?")
        user_yes = HumanMessage(content="yes")
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"name": "jira_create_epic", "args": {}, "id": "call_2", "type": "tool_call"}],
        )
        state = {"messages": [confirmation_msg, user_yes, tool_call_msg]}
        assert should_continue(state) == "tools"

    def test_confirmed_with_full_yes_phrase(self):
        """'yes please go ahead' is treated as affirmative."""
        confirmation_msg = AIMessage(content="Confirm?")
        user_yes = HumanMessage(content="yes please go ahead")
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"name": "confluence_create_page", "args": {}, "id": "call_2", "type": "tool_call"}],
        )
        state = {"messages": [confirmation_msg, user_yes, tool_call_msg]}
        assert should_continue(state) == "tools"

    def test_no_confirmation_stays_human_review(self):
        """If user declined, next attempt still routes to human_review."""
        confirmation_msg = AIMessage(content="Confirm?")
        user_no = HumanMessage(content="no, cancel that")
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"name": "jira_create_epic", "args": {}, "id": "call_2", "type": "tool_call"}],
        )
        state = {"messages": [confirmation_msg, user_no, tool_call_msg]}
        assert should_continue(state) == "human_review"

    def test_insufficient_history_stays_human_review(self):
        """Without prior confirmation messages, high-risk routes to human_review."""
        user_msg = HumanMessage(content="Create a feature for auth")
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"name": "jira_create_epic", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        state = {"messages": [user_msg, tool_call_msg]}
        assert should_continue(state) == "human_review"

    def test_high_risk_tools_constant_contains_expected_names(self):
        """_HIGH_RISK_TOOLS should list all Jira/Confluence write operations."""
        assert "jira_create_epic" in _HIGH_RISK_TOOLS
        assert "jira_create_story" in _HIGH_RISK_TOOLS
        assert "jira_create_sprint" in _HIGH_RISK_TOOLS
        assert "confluence_create_page" in _HIGH_RISK_TOOLS
        assert "confluence_update_page" in _HIGH_RISK_TOOLS
        # Read tools must NOT be in the set
        assert "jira_read_board" not in _HIGH_RISK_TOOLS
        assert "confluence_read_page" not in _HIGH_RISK_TOOLS
        assert "read_codebase" not in _HIGH_RISK_TOOLS


class TestUserConfirmed:
    """Tests for the _user_confirmed() helper."""

    def test_yes(self):
        assert _user_confirmed("yes")

    def test_y(self):
        assert _user_confirmed("y")

    def test_ok(self):
        assert _user_confirmed("ok")

    def test_yes_with_suffix(self):
        assert _user_confirmed("yes please go ahead")

    def test_no(self):
        assert not _user_confirmed("no")

    def test_cancel(self):
        assert not _user_confirmed("cancel")

    def test_empty(self):
        assert not _user_confirmed("")

    def test_case_insensitive(self):
        assert _user_confirmed("YES")
        assert _user_confirmed("Yes please")


# ── make_call_model factory ───────────────────────────────────────────


class TestMakeCallModel:
    """Tests for the make_call_model() factory function."""

    def test_returns_callable(self, monkeypatch):
        """make_call_model must return a callable node function."""
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        fn = make_call_model([])
        assert callable(fn)

    def test_bind_tools_called_with_tool_list(self, monkeypatch):
        """bind_tools() must be called with the provided tools on first invocation (lazy init)."""
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        from langchain_core.tools import tool

        @tool
        def fake_tool(x: str) -> str:
            """A fake tool."""
            return x

        fn = make_call_model([fake_tool])
        # bind_tools is lazy — not called at factory time, only on first invocation.
        mock_llm.bind_tools.assert_not_called()
        fn({"messages": [HumanMessage(content="hi")]})
        mock_llm.bind_tools.assert_called_once_with([fake_tool])

    def test_returned_function_invokes_bound_llm(self, monkeypatch):
        """The returned node function must call the bound LLM (not the raw LLM)."""
        mock_bound_llm = MagicMock()
        fake_response = AIMessage(content="Response with tools")
        mock_bound_llm.invoke.return_value = fake_response

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound_llm
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        fn = make_call_model([])
        state = {"messages": [HumanMessage(content="Hello")]}
        result = fn(state)

        assert result["messages"] == [fake_response]
        mock_bound_llm.invoke.assert_called_once()
        # Raw LLM must NOT be invoked directly
        mock_llm.invoke.assert_not_called()

    def test_returned_function_returns_messages_dict(self, monkeypatch):
        """The node function must return a dict with a 'messages' key."""
        mock_bound_llm = MagicMock()
        mock_bound_llm.invoke.return_value = AIMessage(content="Ok")
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound_llm
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        fn = make_call_model([])
        result = fn({"messages": [HumanMessage(content="Hi")]})
        assert "messages" in result
        assert len(result["messages"]) == 1

    def test_system_prompt_prepended_by_returned_fn(self, monkeypatch):
        """The node function must prepend the system prompt to messages."""
        mock_bound_llm = MagicMock()
        mock_bound_llm.invoke.return_value = AIMessage(content="Ok")
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound_llm
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda: mock_llm)

        fn = make_call_model([])
        fn({"messages": [HumanMessage(content="Plan this")]})

        call_args = mock_bound_llm.invoke.call_args[0][0]
        assert isinstance(call_args[0], SystemMessage)


# ── human_review node ─────────────────────────────────────────────────


class TestHumanReview:
    """Tests for the human_review() node function."""

    def _make_state(self, tool_name: str, args: dict | None = None, msg_id: str | None = None) -> dict:
        msg = AIMessage(
            id=msg_id,
            content="",
            tool_calls=[{"name": tool_name, "args": args or {}, "id": "call_1", "type": "tool_call"}],
        )
        return {"messages": [msg]}

    def test_returns_messages_dict(self):
        """human_review must return a dict with a 'messages' key."""
        state = self._make_state("jira_create_epic", {"title": "Auth"}, msg_id="id-1")
        result = human_review(state)
        assert "messages" in result

    def test_replacement_has_same_id(self):
        """The replacement message must carry the same ID so add_messages replaces it."""
        state = self._make_state("jira_create_epic", msg_id="my-msg-id")
        result = human_review(state)
        assert result["messages"][0].id == "my-msg-id"

    def test_replacement_has_no_tool_calls(self):
        """The replacement message must NOT have tool_calls (routes to END, not tools)."""
        state = self._make_state("jira_create_epic", msg_id="id-1")
        result = human_review(state)
        replacement = result["messages"][0]
        assert not getattr(replacement, "tool_calls", None)

    def test_content_mentions_tool_name(self):
        """Confirmation text must name the tool the agent wants to call."""
        state = self._make_state("jira_create_sprint", msg_id="id-1")
        result = human_review(state)
        assert "jira_create_sprint" in result["messages"][0].content

    def test_content_includes_arg_values(self):
        """Confirmation text should include the tool's argument values."""
        state = self._make_state("confluence_create_page", {"title": "Sprint 1 Plan"}, msg_id="id-1")
        result = human_review(state)
        assert "Sprint 1 Plan" in result["messages"][0].content

    def test_content_asks_for_confirmation(self):
        """Confirmation text must ask the user to confirm."""
        state = self._make_state("jira_create_epic", msg_id="id-1")
        result = human_review(state)
        content = result["messages"][0].content.lower()
        assert "yes" in content or "confirm" in content

    def test_handles_none_id(self):
        """When the original message has no ID, human_review still returns a message."""
        state = self._make_state("jira_create_epic")  # no msg_id
        result = human_review(state)
        assert len(result["messages"]) == 1
        replacement = result["messages"][0]
        assert isinstance(replacement, AIMessage)


class TestHumanReviewImports:
    """Verify human_review is importable from expected locations."""

    def test_importable_from_agent_package(self):
        from scrum_agent.agent import human_review as fn

        assert fn is human_review

    def test_importable_from_nodes_module(self):
        from scrum_agent.agent.nodes import human_review as fn

        assert fn is human_review
