"""The AI layer (plan step 12, autonomy model in plan §2).

Agents run only through `gateway.Gateway`, act by calling the read-only local tools in
`tools`, and return a proposal checked by `gates`. Nothing here appends a workflow
transition: the runner does that when a gate passes.
"""
