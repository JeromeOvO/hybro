const withId = (base: string, id: string) => `${base}/${encodeURIComponent(id)}`

export const routes = {
  home: '/',
  about: '/about',
  openSource: '/open-source',
  pricing: '/pricing',
  privacy: '/privacy',
  chat: '/chat',
  room: (id: string) => withId('/room', id),
  agents: '/agents',
  agent: (id: string) => withId('/agents', id),
  registerAgent: '/agents/new',
  manage: {
    root: '/manage',
    agents: '/manage/agents',
    agent: (id: string) => withId('/manage/agents', id),
    register: '/manage/agents/new',
  },
} as const
