from ktb_core.backend_auth import BackendAuth

from portfolio_builder.competition.repository import ParticipantSettings


class Backend:
    def __init__(self, auth: BackendAuth) -> None:
        self._auth = auth

    def completed(self, participant: ParticipantSettings, portfolio_id: int) -> None:
        self._notify(
            participant,
            {
                "participant_id": participant.participant_id,
                "status": "completed",
                "portfolio_id": portfolio_id,
            },
        )

    def failed(self, participant: ParticipantSettings, error: str) -> None:
        self._notify(
            participant,
            {
                "participant_id": participant.participant_id,
                "status": "failed",
                "error": error,
            },
        )

    def _notify(self, participant: ParticipantSettings, body: dict[str, object]) -> None:
        self._auth.request(
            "POST",
            f"/api/v1/competitions/{participant.competition_id}/portfolio",
            str(participant.user_id),
            json=body,
        )
