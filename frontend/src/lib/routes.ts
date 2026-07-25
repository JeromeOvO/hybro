const withId = (base: string, id: string) => `${base}/${encodeURIComponent(id)}`

export const routes = {
  home: '/',
  about: '/about',
  pricing: '/pricing',
  privacy: '/privacy',
  chat: '/chat',
  room: (id: string) => withId('/room', id),
  agents: '/agents',
  agent: (id: string) => withId('/agents', id),
  hub: '/hub',
  manage: {
    root: '/manage',
    agents: '/manage/agents',
    agent: (id: string) => withId('/manage/agents', id),
    register: '/manage/agents/new',
    apiKeys: '/manage/api-keys',
    inspector: '/manage/inspector',
  },
} as const
