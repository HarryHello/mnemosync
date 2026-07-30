<script setup lang="ts">
import { useRouter } from 'vue-router'
import PromptEditor from '@/components/prompts/PromptEditor.vue'

defineProps<{ name: string }>()

const router = useRouter()

function onBack() {
  router.push('/prompts')
}
</script>

<template>
  <div class="page-container">
    <PromptEditor :name="name" @back="onBack" />
  </div>
</template>
