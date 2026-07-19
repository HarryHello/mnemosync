/** 深色模式 composable */

import { ref, watch } from 'vue'

const isDark = ref(localStorage.getItem('mnemosync_dark') === 'true')

export function useDarkMode() {
  function toggle() {
    isDark.value = !isDark.value
  }

  function setDark(value: boolean) {
    isDark.value = value
  }

  watch(isDark, (value) => {
    localStorage.setItem('mnemosync_dark', String(value))
    document.documentElement.classList.toggle('dark', value)
  }, { immediate: true })

  return {
    isDark,
    toggle,
    setDark,
  }
}
