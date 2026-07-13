<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '@/composables/useAuth'
import { useDarkMode } from '@/composables/useDarkMode'

const router = useRouter()
const { user, isAuthenticated, logout, fetchUser } = useAuth()
const { isDark, toggle: toggleDark } = useDarkMode()

const showMenu = ref(false)

async function handleLogout() {
  await logout()
  router.push('/login')
}

onMounted(async () => {
  if (isAuthenticated.value) {
    await fetchUser()
  }
})
</script>

<template>
  <div class="app" :class="{ dark: isDark }">
    <nav v-if="isAuthenticated" class="navbar">
      <div class="nav-brand">
        <router-link to="/">Mnemosync</router-link>
      </div>

      <div class="nav-links">
        <router-link to="/">Logs</router-link>
        <router-link to="/memories">Memories</router-link>
      </div>

      <div class="nav-actions">
        <button class="icon-btn" @click="toggleDark">
          {{ isDark ? '☀️' : '🌙' }}
        </button>

        <div class="user-menu" @click="showMenu = !showMenu">
          <span class="username">{{ user?.username || 'User' }}</span>
          <div v-if="showMenu" class="dropdown">
            <button @click="handleLogout">Logout</button>
          </div>
        </div>
      </div>
    </nav>

    <main>
      <router-view />
    </main>
  </div>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f5f5f5;
  color: #333;
}

.dark body {
  background: #1a1a1a;
  color: #e0e0e0;
}
</style>

<style scoped>
.app {
  min-height: 100vh;
}

.navbar {
  display: flex;
  align-items: center;
  padding: 0 20px;
  height: 60px;
  background: white;
  border-bottom: 1px solid #eee;
  position: sticky;
  top: 0;
  z-index: 100;
}

.dark .navbar {
  background: #2a2a2a;
  border-color: #444;
}

.nav-brand a {
  font-size: 18px;
  font-weight: bold;
  text-decoration: none;
  color: #2196f3;
}

.nav-links {
  display: flex;
  gap: 20px;
  margin-left: 40px;
}

.nav-links a {
  text-decoration: none;
  color: #666;
  padding: 8px 12px;
  border-radius: 4px;
}

.nav-links a:hover {
  background: #f5f5f5;
}

.dark .nav-links a:hover {
  background: #333;
}

.nav-links a.router-link-active {
  color: #2196f3;
  background: #e3f2fd;
}

.dark .nav-links a.router-link-active {
  background: #1a3a4a;
}

.nav-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 12px;
}

.icon-btn {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  padding: 8px;
  border-radius: 4px;
}

.icon-btn:hover {
  background: #f5f5f5;
}

.dark .icon-btn:hover {
  background: #333;
}

.user-menu {
  position: relative;
  cursor: pointer;
}

.username {
  padding: 8px 12px;
  border-radius: 4px;
}

.username:hover {
  background: #f5f5f5;
}

.dropdown {
  position: absolute;
  right: 0;
  top: 100%;
  background: white;
  border: 1px solid #eee;
  border-radius: 4px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
  min-width: 120px;
}

.dark .dropdown {
  background: #333;
  border-color: #444;
}

.dropdown button {
  display: block;
  width: 100%;
  padding: 10px 16px;
  text-align: left;
  background: none;
  border: none;
  cursor: pointer;
}

.dropdown button:hover {
  background: #f5f5f5;
}

.dark .dropdown button:hover {
  background: #444;
}

main {
  padding: 20px;
}
</style>
