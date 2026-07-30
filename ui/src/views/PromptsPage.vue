<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import PromptListTab from '@/components/prompts/PromptListTab.vue'
import PersonaEditorTab from '@/components/prompts/PersonaEditorTab.vue'

const router = useRouter()
const activeTab = ref('prompts')

function onEdit(name: string) {
  router.push({ name: 'prompt-edit', params: { name } })
}
</script>

<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="page-tabs">
      <el-tab-pane label="提示词" name="prompts">
        <PromptListTab @edit="onEdit" />
      </el-tab-pane>
      <el-tab-pane label="人格" name="persona">
        <PersonaEditorTab :active="activeTab === 'persona'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
