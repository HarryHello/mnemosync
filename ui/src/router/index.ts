import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '@/api/client'
import AuthRoutes from './AuthRoutes'
import MainRoutes from './MainRoutes'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    AuthRoutes,
    MainRoutes,
    {
      path: '/:pathMatch(.*)*',
      redirect: '/dashboard',
    },
  ],
})

router.beforeEach((to) => {
  const isAuthenticated = !!getToken()
  const requiresAuth = to.matched.some((r) => r.meta.requiresAuth)

  if (requiresAuth && !isAuthenticated) {
    return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
  }

  if (to.name === 'login' && isAuthenticated) {
    return { path: '/dashboard' }
  }
})

export default router
