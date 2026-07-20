import type { RouteRecordRaw } from 'vue-router'

const AuthRoutes: RouteRecordRaw[] = [
  {
    path: '/login',
    component: () => import('@/layouts/blank/BlankLayout.vue'),
    meta: { requiresAuth: false },
    children: [
      {
        path: '',
        name: 'login',
        component: () => import('@/views/LoginPage.vue'),
      },
    ],
  },
  {
    path: '/setup',
    component: () => import('@/layouts/blank/BlankLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'setup',
        component: () => import('@/views/SetupPage.vue'),
      },
    ],
  },
]

export default AuthRoutes
