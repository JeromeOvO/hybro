from common.dto import GatewayRequest


def gateway_request_from_payload(agent_id: str, payload: dict) -> GatewayRequest:
    return GatewayRequest(agent_id=agent_id, payload=payload)


__all__ = ["gateway_request_from_payload"]
