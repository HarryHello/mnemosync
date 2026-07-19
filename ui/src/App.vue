<script setup lang="ts">
import { onMounted } from 'vue'
import { useDarkMode } from '@/composables/useDarkMode'
import { useAuthStore } from '@/stores/auth'

// Initialize dark-mode watcher immediately (composable applies <html>.dark on setup)
useDarkMode()

const authStore = useAuthStore()

onMounted(() => {
  if (authStore.isAuthenticated) {
    void authStore.fetchUser().catch(() => undefined)
  }
})
</script>

<template>
  <router-view />
</template>
