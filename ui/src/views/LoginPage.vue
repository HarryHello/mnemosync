<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref<FormInstance | null>(null)
const submitting = ref(false)

const form = reactive({
  username: '',
  password: '',
})

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const result = await authStore.login(form.username, form.password)
    ElMessage.success('登录成功')
    await authStore.fetchUser().catch(() => undefined)
    if (result.must_change_password) {
      router.push('/setup')
      return
    }
    const redirect = route.query.redirect
    router.push(typeof redirect === 'string' ? redirect : '/dashboard')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card">
      <div class="brand">
        <img class="brand-mark" src="/favicon.svg" alt="Mnemosync" />
        <h1>Mnemosync</h1>
        <p class="subtitle">管理面板</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="onSubmit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" autocomplete="username" placeholder="用户名" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            autocomplete="current-password"
            placeholder="密码"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          :loading="submitting"
          native-type="submit"
          class="submit"
        >
          登录
        </el-button>
      </el-form>

      <el-alert
        type="info"
        :closable="false"
        title="默认凭证"
        description="首次登录用户名 mnemosync / 密码 mnemosync, 登录后系统会提示修改密码。"
      />
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.login-page {
  min-height: 100vh;
  @include flex-center;
  padding: $space-5;
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: $space-3 $space-2;
  border: 1px solid rgba(66, 133, 244, 0.1);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.06);
}

.brand {
  text-align: center;
  margin-bottom: $space-6;

  .brand-mark {
    display: block;
    width: 56px;
    height: 56px;
    margin: 0 auto $space-2;
    border-radius: $radius-lg;
    padding: 8px;
    background: rgba(66, 133, 244, 0.08);
    border: 1px solid rgba(66, 133, 244, 0.12);
  }

  h1 {
    font-size: 24px;
    font-weight: 700;
    color: var(--el-text-color-primary);
    letter-spacing: -0.03em;
  }

  .subtitle {
    color: var(--el-text-color-secondary);
    margin-top: $space-1;
    font-size: 13px;
  }
}

.submit {
  width: 100%;
  margin-bottom: $space-4;
}
</style>
