class ToolError(Exception):
    """A failure the model can correct; it is returned to the model instead of ending the run."""


class PortfolioRejected(ToolError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(
            "The portfolio was not saved. Fix every error and call submit_portfolio again:\n- "
            + "\n- ".join(errors)
        )


class UnknownCompany(ToolError):
    pass


class NoMarketData(ToolError):
    pass


class GraphTimeout(ToolError):
    pass
