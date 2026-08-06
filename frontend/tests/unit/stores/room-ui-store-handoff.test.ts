import { describe, it, expect, beforeEach } from "vitest"
import { useRoomUiStore } from "@/stores/room-ui-store"

describe("PendingRoomData handoffMode", () => {
  beforeEach(() => {
    useRoomUiStore.setState({ pendingRoomData: {}, pendingChatDraft: null })
  })

  it("stores and consumes handoffMode: prefill", () => {
    const store = useRoomUiStore.getState()
    store.setPendingRoomData("room-1", {
      initialMessage: "Hello world",
      handoffMode: "prefill",
    })

    const data = useRoomUiStore.getState().consumePendingRoomData("room-1")
    expect(data).not.toBeNull()
    expect(data!.initialMessage).toBe("Hello world")
    expect(data!.handoffMode).toBe("prefill")
  })

  it("defaults to undefined handoffMode (backward compat with autosend)", () => {
    const store = useRoomUiStore.getState()
    store.setPendingRoomData("room-2", {
      initialMessage: "Legacy message",
      targetGroup: "all_agents",
    })

    const data = useRoomUiStore.getState().consumePendingRoomData("room-2")
    expect(data).not.toBeNull()
    expect(data!.handoffMode).toBeUndefined()
  })

  it("scopes handoffMode per roomId", () => {
    const store = useRoomUiStore.getState()
    store.setPendingRoomData("room-a", {
      initialMessage: "Prefill msg",
      handoffMode: "prefill",
    })
    store.setPendingRoomData("room-b", {
      initialMessage: "Autosend msg",
    })

    const dataA = useRoomUiStore.getState().consumePendingRoomData("room-a")
    const dataB = useRoomUiStore.getState().consumePendingRoomData("room-b")
    expect(dataA!.handoffMode).toBe("prefill")
    expect(dataB!.handoffMode).toBeUndefined()
  })

  it("keeps an Agent mention until the chat composer clears it", () => {
    const store = useRoomUiStore.getState()
    store.setPendingChatDraft("<@agent-1|Weather Agent> ")

    expect(useRoomUiStore.getState().pendingChatDraft).toBe(
      "<@agent-1|Weather Agent> ",
    )

    useRoomUiStore.getState().clearPendingChatDraft()
    expect(useRoomUiStore.getState().pendingChatDraft).toBeNull()
  })
})
