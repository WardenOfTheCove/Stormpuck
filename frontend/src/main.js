import './style.css'

document.querySelector('#app').innerHTML = `
  <main class="flex min-h-screen flex-col items-center justify-center gap-6 bg-slate-950 px-6 text-center text-white">
    <div>
      <p class="mb-3 text-sm font-semibold uppercase tracking-[0.3em] text-cyan-400">Flask + Vite</p>
      <h1 class="text-4xl font-bold tracking-tight sm:text-6xl">Project ready</h1>
      <p class="mx-auto mt-4 max-w-lg text-slate-300">
        Your Tailwind CSS frontend is connected to a Flask backend.
      </p>
    </div>
    <button
      id="health-check"
      class="rounded-lg bg-cyan-400 px-5 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300"
      type="button"
    >
      Check backend
    </button>
    <p id="health-status" class="min-h-6 text-sm text-slate-400" aria-live="polite"></p>
  </main>
`

document.querySelector('#health-check').addEventListener('click', async () => {
  const status = document.querySelector('#health-status')
  status.textContent = 'Checking...'

  try {
    const response = await fetch('/api/health')
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`)

    const data = await response.json()
    status.textContent = `Backend status: ${data.status}`
  } catch (error) {
    status.textContent = `Backend unavailable: ${error.message}`
  }
})
