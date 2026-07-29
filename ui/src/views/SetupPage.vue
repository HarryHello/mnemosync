<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { setupCredentials, setToken } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const formRef = ref<FormInstance | null>(null)
const submitting = ref(false)

const form = reactive({
  old_password: '',
  new_username: '',
  new_password: '',
  confirm: '',
})

const rules: FormRules = {
  old_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_username: [
    { required: true, message: '请输入新用户名', trigger: 'blur' },
    { min: 1, max: 50, message: '用户名长度 1-50 位', trigger: 'blur' },
  ],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_r, value: string, cb) => {
        if (value !== form.new_password) cb(new Error('两次输入不一致'))
        else cb()
      },
      trigger: 'blur',
    },
  ],
}

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    await setupCredentials({
      old_password: form.old_password,
      new_username: form.new_username,
      new_password: form.new_password,
    })
    ElMessage.success('账号密码已设置, 请重新登录')
    await authStore.logout().catch(() => undefined)
    setToken(null)
    router.push('/login')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="setup-page">
    <el-card class="setup-card">
      <div class="brand">
        <img class="brand-mark" src="/favicon.svg" alt="Mnemosync" />
        <h1>首次使用</h1>
        <p class="subtitle">请设置你的账号和密码</p>
      </div>

      <el-alert
        type="warning"
        :closable="false"
        title="必须完成设置才能进入面板"
        description="为了安全, 首次登录必须修改默认账号密码; 未完成前无法访问其他页面。"
        show-icon
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        style="margin-top: 16px"
        @submit.prevent="onSubmit"
      >
        <el-form-item label="当前密码" prop="old_password">
          <el-input
            v-model="form.old_password"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="mnemosync"
          />
        </el-form-item>
        <el-form-item label="新用户名" prop="new_username">
          <el-input
            v-model="form.new_username"
            autocomplete="username"
            placeholder="1-50 位"
          />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="form.new_password"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="至少 6 位, 不能是默认密码"
          />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            show-password
            autocomplete="new-password"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          :loading="submitting"
          native-type="submit"
          class="submit"
        >
          完成设置
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.setup-page {
  min-height: 100vh;
  @include flex-center;
  padding: $space-5;
}

.setup-card {
  width: 100%;
  max-width: 460px;
  padding: $space-3 $space-2;
  border: 1px solid rgba(66, 133, 244, 0.1);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.06);
}

.brand {
  text-align: center;
  margin-bottom: $space-4;

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
  margin-top: $space-2;
}
</style>
