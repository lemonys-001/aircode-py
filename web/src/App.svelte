<script>
  import { onMount } from 'svelte'
  import { marked } from 'marked'
  import DOMPurify from 'dompurify'

  marked.setOptions({ gfm: true, breaks: true })

  let status = { logged_in: false, version: '', model: '' }
  let models = []
  let sessions = []
  let active = null
  let messages = []
  let live = null
  let input = ''
  let busy = false
  let permission = null
  let error = ''
  let model = 'deepseek-v4.1-flash-free'
  let sidebar = false
  let tokens = ''
  let abortController = null
  let scrollQueued = false

  const params = new URLSearchParams(location.search)
  const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''))
  const tokenParam = params.get('token') || hashParams.get('token')
  if (tokenParam) {
    localStorage.setItem('aircode.token', tokenParam)
    params.delete('token')
    hashParams.delete('token')
    const query = params.toString()
    const hash = hashParams.toString()
    history.replaceState(
      {},
      '',
      location.pathname + (query ? `?${query}` : '') + (hash ? `#${hash}` : ''),
    )
  }
  const token = tokenParam || localStorage.getItem('aircode.token') || ''

  function authHeaders(extra = {}) {
    return token ? { Authorization: `Bearer ${token}`, ...extra } : extra
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: authHeaders(options.headers || {}),
    })
    if (!response.ok) {
      const raw = await response.text()
      let message = raw
      try {
        message = JSON.parse(raw).detail || raw
      } catch {
        /* keep raw */
      }
      throw new Error(message || `HTTP ${response.status}`)
    }
    return response.json()
  }

  function render(content) {
    return DOMPurify.sanitize(marked.parse(content || ''))
  }

  function modelId(value) {
    return (value || '').replace(/^opencode\//, '')
  }

  function shortModel(value) {
    const id = modelId(value)
    const found = models.find((item) => item.id === id)
    return found ? found.name : id
  }

  onMount(async () => {
    try {
      status = await api('/api/status')
      model = modelId(status.model || model)
      sessions = await api('/api/sessions')
      if (sessions.length) await openSession(sessions[0].id)
      if (!status.logged_in) {
        error = 'Not logged in. Run `aircode auth login` in a terminal.'
      }
      api('/api/models')
        .then((items) => (models = items))
        .catch(() => {})
    } catch (err) {
      error = err.message
    }
  })

  async function openSession(id) {
    if (busy) return
    try {
      const data = await api(`/api/sessions/${id}`)
      active = data.session
      messages = data.messages.filter((message) => message.role !== 'system')
      live = null
      error = ''
      sidebar = false
      scrollSoon()
    } catch (err) {
      error = err.message
    }
  }

  function newSession() {
    if (busy) return
    active = null
    messages = []
    live = null
    error = ''
    input = ''
    sidebar = false
  }

  function commit() {
    if (!live) return
    if (live.content || live.tools.length || live.error) {
      messages = [
        ...messages,
        { role: 'assistant', content: live.content, tools: live.tools, error: live.error },
      ]
    }
    live = null
  }

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    if (!status.logged_in) {
      error = 'Not logged in. Run `aircode auth login` in a terminal.'
      return
    }
    input = ''
    error = ''
    busy = true
    tokens = ''
    messages = [...messages, { role: 'user', content: text }]
    live = { content: '', tools: [], error: '' }
    scrollSoon()
    const controller = new AbortController()
    abortController = controller
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ session_id: active ? active.id : null, text, model }),
        signal: controller.signal,
      })
      if (!response.ok) {
        const raw = await response.text()
        let message = raw
        try {
          message = JSON.parse(raw).detail || raw
        } catch {
          /* keep raw */
        }
        throw new Error(message)
      }
      await readSSE(response, handleEvent)
    } catch (err) {
      if (err.name !== 'AbortError') {
        error = err.message
        if (live) live = { ...live, error: err.message }
      }
    } finally {
      busy = false
      abortController = null
      if (live && (live.content || live.tools.length || live.error)) commit()
      scrollSoon()
      refreshSessions()
    }
  }

  async function readSSE(response, handler) {
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let index
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        let data = ''
        for (const line of block.split('\n')) {
          if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (data) {
          try {
            handler(JSON.parse(data))
          } catch {
            /* ignore malformed event */
          }
        }
      }
    }
  }

  function handleEvent(event) {
    if (!live) live = { content: '', tools: [], error: '' }
    const kind = event.type
    if (kind === 'session') {
      if (event.id) {
        active = {
          id: event.id,
          title: event.title || 'Untitled',
          model: event.model || model,
        }
      }
      if (event.title) document.title = `Aircode - ${event.title}`
    } else if (kind === 'text') {
      live = { ...live, content: live.content + (event.text || '') }
      scrollSoon()
    } else if (kind === 'tool_start') {
      live = {
        ...live,
        tools: [
          ...live.tools,
          {
            name: event.name,
            summary: event.summary,
            output: '',
            pending: true,
            denied: false,
          },
        ],
      }
      scrollSoon()
    } else if (kind === 'tool_end') {
      const tools = [...live.tools]
      for (let index = tools.length - 1; index >= 0; index -= 1) {
        if (tools[index].pending) {
          tools[index] = {
            ...tools[index],
            output: event.output || '',
            pending: false,
            denied: !!event.denied,
          }
          break
        }
      }
      live = { ...live, tools }
      scrollSoon()
    } else if (kind === 'permission') {
      permission = { id: event.id, request: event.request }
    } else if (kind === 'usage') {
      const total =
        event.usage && (event.usage.total_tokens || event.usage.completion_tokens)
      if (total) tokens = String(total)
    } else if (kind === 'error') {
      error = event.message || 'Error'
      live = { ...live, error }
    } else if (kind === 'done') {
      busy = false
    }
  }

  async function resolvePermission(allow) {
    if (!permission) return
    try {
      await api(`/api/permission/${permission.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ allow }),
      })
    } catch (err) {
      error = err.message
    }
    permission = null
  }

  function stop() {
    if (abortController) abortController.abort()
    if (active) api(`/api/cancel/${active.id}`, { method: 'POST' }).catch(() => {})
    busy = false
  }

  async function refreshSessions() {
    try {
      sessions = await api('/api/sessions')
    } catch {
      /* session refresh is best effort */
    }
  }

  function scrollSoon() {
    if (scrollQueued) return
    scrollQueued = true
    requestAnimationFrame(() => {
      scrollQueued = false
      const element = document.getElementById('messages')
      if (element) element.scrollTop = element.scrollHeight
    })
  }

  function onKey(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send()
    }
  }

  function changeModel() {
    if (active) active = { ...active, model }
  }
</script>

<div class="app">
  <aside class:open={sidebar}>
    <div class="side-brand">Aircode</div>
    <button class="new" on:click={newSession}>New chat</button>
    <nav class="sessions" aria-label="Sessions">
      {#each sessions as session}
        <button
          class="session"
          class:active={active && active.id === session.id}
          on:click={() => openSession(session.id)}
        >
          <span class="session-title">{session.title}</span>
          <span class="session-meta">{session.model ? shortModel(session.model) : ''}</span>
        </button>
      {:else}
        <p class="empty-small">No sessions yet.</p>
      {/each}
    </nav>
    <div class="side-foot">Aircode {status.version || ''}</div>
  </aside>

  {#if sidebar}
    <button class="backdrop" aria-label="Close menu" on:click={() => (sidebar = false)}></button>
  {/if}

  <main class="main">
    <header>
      <button class="menu" on:click={() => (sidebar = !sidebar)} aria-label="Sessions">
        Menu
      </button>
      <select bind:value={model} on:change={changeModel} aria-label="Model">
        {#each models as item}
          <option value={item.id}>{item.name}{item.free ? ' (free)' : ''}</option>
        {/each}
      </select>
      <span class="grow"></span>
      {#if busy}<span class="status">Working</span>{/if}
      {#if tokens}<span class="status">{tokens} tokens</span>{/if}
      {#if busy}<button class="ghost" on:click={stop}>Stop</button>{/if}
    </header>

    <section class="messages" id="messages">
      {#if !messages.length && !live}
        <div class="empty">
          <h1>Start with a task.</h1>
          <p>
            Ask about this project, read code, edit files, or run a command. Aircode
            asks before it writes or executes anything.
          </p>
          {#if !status.logged_in}
            <p class="warn">Not logged in. Run <code>aircode auth login</code> in a terminal.</p>
          {/if}
          {#if error}
            <p class="warn">{error}</p>
          {/if}
        </div>
      {/if}

      {#each messages as message}
        {#if message.role === 'user'}
          <article class="msg user">
            <div class="who">You</div>
            <div class="text">{message.content}</div>
          </article>
        {:else if message.role === 'assistant' && message.content}
          <article class="msg assistant">
            <div class="who">Aircode</div>
            <div class="text markdown">{@html render(message.content)}</div>
            {#each message.tools || [] as tool}
              <div class="tool">
                <div class="tool-head">{tool.summary || tool.name}</div>
                {#if tool.output}<pre>{tool.output}</pre>{/if}
              </div>
            {/each}
            {#if message.error}<div class="error">{message.error}</div>{/if}
          </article>
        {:else if message.role === 'tool'}
          <div class="tool">
            <div class="tool-head">{message.name || 'tool'}</div>
            <pre>{message.content}</pre>
          </div>
        {/if}
      {/each}

      {#if live}
        <article class="msg assistant">
          <div class="who">Aircode</div>
          {#if live.content}
            <div class="text markdown">{@html render(live.content)}</div>
          {/if}
          {#each live.tools as tool}
            <div class="tool">
              <div class="tool-head">
                {tool.summary || tool.name}
                {#if tool.pending}<span class="pending">running</span>{/if}
                {#if tool.denied}<span class="pending">denied</span>{/if}
              </div>
              {#if tool.output}<pre>{tool.output}</pre>{/if}
            </div>
          {/each}
          {#if live.error}<div class="error">{live.error}</div>{/if}
        </article>
      {/if}
    </section>

    {#if error && messages.length}
      <div class="banner">
        <span>{error}</span>
        <button class="ghost" on:click={() => (error = '')}>Dismiss</button>
      </div>
    {/if}

    <footer class="composer">
      <textarea
        bind:value={input}
        on:keydown={onKey}
        placeholder="Message Aircode..."
        rows="1"
        disabled={!status.logged_in}
      ></textarea>
      <button
        class="send"
        on:click={send}
        disabled={busy || !input.trim() || !status.logged_in}
      >
        Send
      </button>
    </footer>
  </main>

  {#if permission}
    <div class="overlay" role="dialog" aria-modal="true" aria-label="Permission request">
      <div class="dialog">
        <h2>Allow {permission.request.tool}?</h2>
        <p class="muted">Review the request before allowing it.</p>
        <pre class="preview">{permission.request.preview}</pre>
        <div class="actions">
          <button class="ghost" on:click={() => resolvePermission(false)}>Deny</button>
          <button class="send" on:click={() => resolvePermission(true)}>Allow</button>
        </div>
      </div>
    </div>
  {/if}
</div>
