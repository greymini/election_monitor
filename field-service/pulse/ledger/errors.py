class LedgerError(Exception):
    """Base for ledger-service errors. Messages are for logs and the API, never shown in the bot."""


class NotFound(LedgerError):
    pass


class Invalid(LedgerError):
    pass


class Forbidden(LedgerError):
    pass


class PiiRejected(Invalid):
    def __init__(self, field: str, kinds: list[str]):
        super().__init__(f"{field} contains an identifier ({', '.join(kinds)})")
        self.field = field
        self.kinds = kinds
