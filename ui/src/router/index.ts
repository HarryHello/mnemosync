import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import AuthRoutes from './AuthRoutes'
import MainRoutes from './MainRoutes'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    ...AuthRoutes,
    MainRoutes,
    {
      path: '/:pathMatch(.*)*',
      redirect: '/dashboard',
    },
  ],
})

router.beforeEach(async (to) => {
  const isAuthenticated = !!getToken()
  const requiresAuth = to.matched.some((r) => r.meta.requiresAuth)

  // must_change_password 硬拦: 已登录但未完成首次设置的用户只能停在 /setup;
  // 拿不到 user 时兜底调 fetchUser (刷新页面后 store 空). 401 会自动清 token,
  // 之后 isAuthenticated 变 false, 进入下方未鉴权分支跳 /login.
  if (isAuthenticated) {
    const authStore = useAuthStore()
    let user = authStore.user
    if (!user) {
      user = await authStore.fetchUser().catch(() => null)
    }
    const stillAuthed = !!getToken()

    if (!stillAuthed) {
      if (requiresAuth) {
        return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
      }
      return
    }

    if (to.name === 'login') {
      return user?.must_change_password ? { name: 'setup' } : { path: '/dashboard' }
    }
    if (user?.must_change_password && to.name !== 'setup') {
      return { name: 'setup' }
    }
    if (!user?.must_change_password && to.name === 'setup') {
      return { path: '/dashboard' }
    }
    return
  }

  if (requiresAuth) {
    return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
  }
})

export default router
