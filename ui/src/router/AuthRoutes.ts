import type { RouteRecordRaw } from 'vue-router'

const AuthRoutes: RouteRecordRaw = {
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
}

export default AuthRoutes
