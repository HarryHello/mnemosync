<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '@/composables/useAuth'

const router = useRouter()
const { login } = useAuth()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!username.value || !password.value) {
    error.value = 'Please enter username and password'
    return
  }

  loading.value = true
  error.value = ''

  try {
    await login(username.value, password.value)
    router.push('/')
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Login failed'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-view">
    <div class="login-card">
      <h1>Mnemosync</h1>
      <p>Admin Console</p>

      <form @submit.prevent="handleLogin">
        <div class="form-group">
          <label>Username</label>
          <input v-model="username" type="text" placeholder="Username" :disabled="loading" />
        </div>

        <div class="form-group">
          <label>Password</label>
          <input v-model="password" type="password" placeholder="Password" :disabled="loading" />
        </div>

        <div class="error" v-if="error">{{ error }}</div>

        <button type="submit" :disabled="loading">
          {{ loading ? 'Logging in...' : 'Login' }}
        </button>
      </form>

      <div class="hint">
        Default: mnemosync / mnemosync
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f5f5f5;
}

.login-card {
  background: white;
  padding: 40px;
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
  width: 360px;
}

h1 {
  margin: 0;
  font-size: 28px;
  text-align: center;
}

p {
  text-align: center;
  color: #666;
  margin-bottom: 30px;
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 6px;
  font-size: 14px;
  color: #333;
}

.form-group input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
  box-sizing: border-box;
}

.form-group input:focus {
  outline: none;
  border-color: #2196f3;
}

.error {
  color: #f44336;
  font-size: 14px;
  margin-bottom: 16px;
}

button {
  width: 100%;
  padding: 12px;
  background: #2196f3;
  color: white;
  border: none;
  border-radius: 4px;
  font-size: 16px;
  cursor: pointer;
}

button:hover:not(:disabled) {
  background: #1976d2;
}

button:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.hint {
  margin-top: 20px;
  text-align: center;
  font-size: 12px;
  color: #999;
}
</style>
