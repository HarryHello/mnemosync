import type { RouteRecordRaw } from 'vue-router'

const MainRoutes: RouteRecordRaw = {
  path: '/',
  component: () => import('@/layouts/full/FullLayout.vue'),
  meta: { requiresAuth: true },
  redirect: '/dashboard',
  children: [
    {
      path: 'dashboard',
      name: 'dashboard',
      component: () => import('@/views/DashboardPage.vue'),
    },
    {
      path: 'prompts',
      name: 'prompts',
      component: () => import('@/views/PromptsPage.vue'),
    },
    {
      path: 'prompts/:name',
      name: 'prompt-edit',
      component: () => import('@/views/PromptEditPage.vue'),
      props: true,
    },
    {
      path: 'api-keys',
      name: 'api-keys',
      component: () => import('@/views/ApiKeysPage.vue'),
    },
    {
      path: 'identity',
      name: 'identity',
      component: () => import('@/views/IdentityPage.vue'),
    },
    {
      path: 'logs',
      name: 'logs',
      component: () => import('@/views/LogsPage.vue'),
    },
    {
      path: 'memories',
      name: 'memories',
      component: () => import('@/views/MemoriesPage.vue'),
    },
    {
      path: 'relationships',
      name: 'relationships',
      component: () => import('@/views/RelationshipsPage.vue'),
    },
    {
      path: 'upstream',
      name: 'upstream',
      component: () => import('@/views/UpstreamPage.vue'),
    },
    {
      path: 'models',
      name: 'models',
      component: () => import('@/views/ModelsPage.vue'),
    },
    {
      path: 'debug-chat',
      name: 'debug-chat',
      component: () => import('@/views/DebugChatPage.vue'),
    },
    {
      path: 'settings',
      name: 'settings',
      component: () => import('@/views/SettingsPage.vue'),
    },
  ],
}

export default MainRoutes
