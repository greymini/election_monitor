class TeamError(Exception):
    """Base for team-service errors; the API maps each subclass to an HTTP status."""


class NotFound(TeamError):
    pass


class Invalid(TeamError):
    pass


class Conflict(TeamError):
    pass
