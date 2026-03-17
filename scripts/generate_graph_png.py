"""Generate a PNG visualisation of the current LangGraph agent graph.

# See README: "Agentic Blueprint Reference" — Agent Graph
#
# LangGraph's compiled graphs expose .get_graph() which returns a DrawableGraph.
# DrawableGraph has .draw_mermaid_png() which calls the Mermaid.ink API to render
# a PNG from the graph topology. This requires no additional dependencies — it
# uses an HTTP request to mermaid.ink under the hood.
#
# Usage:
#   make graph
#   # or directly:
#   uv run python scripts/generate_graph_png.py
"""

from __future__ import annotations

from pathlib import Path

from scrum_agent.agent.graph import create_graph


def main() -> None:
    """Build the agent graph and save a PNG visualisation to docs/graph.png."""
    graph = create_graph()

    # get_graph() returns a DrawableGraph — a lightweight representation of
    # the graph topology (nodes + edges) that can be rendered in various formats.
    # draw_mermaid_png() converts it to Mermaid markup, sends it to the
    # mermaid.ink rendering API, and returns raw PNG bytes.
    # See README: "Agentic Blueprint Reference" — graph visualisation
    png_bytes = graph.get_graph().draw_mermaid_png()

    output_path = Path(__file__).resolve().parent.parent / "docs" / "graph.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(png_bytes)

    print(f"Graph PNG saved to {output_path}")


if __name__ == "__main__":
    main()
