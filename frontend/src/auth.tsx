import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, setUnauthorizedHandler, tokenStore, type Me } from './api'

interface AuthState {
  me: Me | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  can: (scope: string) => boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setUnauthorizedHandler(() => {
      tokenStore.clear()
      setMe(null)
    })
    if (!tokenStore.get()) {
      setLoading(false)
      return
    }
    api<Me>('/internal/auth/me')
      .then(setMe)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await api<{ token: string; user: Me }>('/internal/auth/login', { body: { username, password } })
    tokenStore.set(res.token)
    setMe(res.user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await api('/internal/auth/logout', { method: 'POST' })
    } finally {
      tokenStore.clear()
      setMe(null)
    }
  }, [])

  const can = useCallback((scope: string) => !!me?.scopes.includes(scope), [me])

  return <AuthContext.Provider value={{ me, loading, login, logout, can }}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}
