/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useRef } from 'react';

export default function App() {
  const [activeTab, setActiveTab] = useState<'chat' | 'work' | 'terminal' | 'settings' | 'astyria' | 'fourAgents'>('chat');
  const [messages, setMessages] = useState<Array<{ role: 'user' | 'assistant'; text: string; agent: string; feedback?: string }>>([]);
  const [inputMsg, setInputMsg] = useState('');
  const [selectedAgent, setSelectedAgent] = useState('astyria');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [cosmicAssets] = useState([
    { name: 'Mars (Czerwona Planeta)', status: 'Nabyty dla GUI Color Palette', role: 'Baza danych terraformacji interfejsu.', locked: false },
    { name: 'Wenus (Siostra Ziemi)', status: 'Nabyta dla Glassmorphism', role: 'Atmosferyczne cieniowanie i filtry.', locked: false },
    { name: 'Jowisz & Satelity', status: 'Nabyty dla GPU Acceleration Grid', role: 'Reaktor obliczeniowy układu.', locked: false },
    { name: 'Pierścienie Saturna', status: 'Nabyte dla Border Glow & Particles', role: 'Orbity nawigacyjne systemu.', locked: false },
    { name: 'Ziemia (Planeta Macierzysta)', status: '[ZABLOKOWANA · NIE DO ZAKUPU]', role: 'Wyłączona z handlu. Dom użytkownika.', locked: true },
  ]);
  const [debateTopic, setDebateTopic] = useState('Optymalizacja interfejsu systemu Debian 13 i akwizycja wszechświata poza Ziemią');
  const [debateLog, setDebateLog] = useState<Array<{ name: string; color: string; text: string }>>([]);
  const [isDebating, setIsDebating] = useState(false);
  const transcriptRef = useRef<HTMLDivElement>(null);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputMsg.trim()) return;
    const userText = inputMsg.trim();
    setInputMsg('');
    setMessages(prev => [...prev, { role: 'user', text: userText, agent: selectedAgent }]);

    try {
      const res = await fetch('/api/v1/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile: selectedAgent, message: userText })
      });
      if (!res.ok) throw new Error('Brak potwierdzonej odpowiedzi z API.');
      const data = await res.json();
      if (typeof data.content !== 'string' || !data.content.trim()) throw new Error('API nie zwróciło odpowiedzi modelu.');
      setMessages(prev => [...prev, { role: 'assistant', text: data.content, agent: selectedAgent }]);
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', text: 'Błąd połączenia z rzeczywistym modelem AI. Nie wygenerowano zastępczej odpowiedzi.', agent: 'System' }]);
    }
  };

  const startDebate = () => {
    // This legacy demo has no authenticated multi-agent gateway.
    // Do not invent a discussion, worker status or completed actions.
    setDebateLog([{
      name: 'System',
      color: '#94a3b8',
      text: 'Czat grupowy nie jest połączony z czterema uruchomionymi agentami. Debata nie została wykonana; konieczny jest rzeczywisty worker i brama komunikacji.',
    }]);
    setIsDebating(false);
  };

  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div style={{ minHeight: '100dvh', background: '#050811', color: '#f1f5f9', fontFamily: 'Inter, system-ui, sans-serif', display: 'flex', flexDirection: 'column' }}>
      {/* Topbar */}
      <header style={{ height: '64px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', padding: '0 20px', background: '#0b1322', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ width: '38px', height: '38px', borderRadius: '10px', background: 'rgba(56,189,248,0.15)', border: '1px solid #38bdf8', display: 'grid', placeItems: 'center', color: '#38bdf8', fontWeight: 'bold' }}>D13</div>
          <div>
            <strong>Debian 13 Trixie</strong>
            <span style={{ display: 'block', fontSize: '11px', color: '#94a3b8' }}>Astyria Cosmic Edition · Bastilla Shan</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#34d399', display: 'inline-block' }}></span>
          <span style={{ fontSize: '12px', color: '#94a3b8' }}>Status modelu i uprawnienia do weryfikacji</span>
        </div>
      </header>

      {/* Main Layout */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Sidebar */}
        <aside style={{ width: sidebarCollapsed ? '70px' : '260px', borderRight: '1px solid #1e293b', background: '#0b1322', display: 'flex', flexDirection: 'column', transition: 'width 0.2s ease', padding: '16px' }}>
          <button onClick={() => setSidebarCollapsed(!sidebarCollapsed)} style={{ background: 'none', border: '1px solid #1e293b', color: '#cbd5e1', borderRadius: '8px', padding: '8px', cursor: 'pointer', marginBottom: '16px', textAlign: 'left', fontSize: '12px' }}>
            {sidebarCollapsed ? '📂' : '📂 Zwiń menu boczne'}
          </button>
          
          <nav style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <button onClick={() => setActiveTab('chat')} style={{ padding: '10px 12px', borderRadius: '8px', border: 'none', background: activeTab === 'chat' ? 'rgba(56,189,248,0.15)' : 'transparent', color: activeTab === 'chat' ? '#38bdf8' : '#cbd5e1', textAlign: 'left', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span>💬</span>{!sidebarCollapsed && <span>Czat AI</span>}
            </button>
            <button onClick={() => setActiveTab('work')} style={{ padding: '10px 12px', borderRadius: '8px', border: 'none', background: activeTab === 'work' ? 'rgba(56,189,248,0.15)' : 'transparent', color: activeTab === 'work' ? '#38bdf8' : '#cbd5e1', textAlign: 'left', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span>📁</span>{!sidebarCollapsed && <span>ChatGPT Work</span>}
            </button>
            <button onClick={() => setActiveTab('terminal')} style={{ padding: '10px 12px', borderRadius: '8px', border: 'none', background: activeTab === 'terminal' ? 'rgba(56,189,248,0.15)' : 'transparent', color: activeTab === 'terminal' ? '#38bdf8' : '#cbd5e1', textAlign: 'left', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span>💻</span>{!sidebarCollapsed && <span>Terminal API</span>}
            </button>
            <button onClick={() => setActiveTab('fourAgents')} style={{ padding: '10px 12px', borderRadius: '8px', border: 'none', background: activeTab === 'fourAgents' ? 'rgba(251,191,36,0.15)' : 'transparent', color: activeTab === 'fourAgents' ? '#fbbf24' : '#cbd5e1', textAlign: 'left', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span>🤖</span>{!sidebarCollapsed && <span>Debata 4 Agentów</span>}
            </button>
            <button onClick={() => setActiveTab('astyria')} style={{ padding: '10px 12px', borderRadius: '8px', border: 'none', background: activeTab === 'astyria' ? 'rgba(56,189,248,0.15)' : 'transparent', color: activeTab === 'astyria' ? '#38bdf8' : '#cbd5e1', textAlign: 'left', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span>✨</span>{!sidebarCollapsed && <span>Astyria Design</span>}
            </button>
            <button onClick={() => setActiveTab('settings')} style={{ padding: '10px 12px', borderRadius: '8px', border: 'none', background: activeTab === 'settings' ? 'rgba(56,189,248,0.15)' : 'transparent', color: activeTab === 'settings' ? '#38bdf8' : '#cbd5e1', textAlign: 'left', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span>⚙️</span>{!sidebarCollapsed && <span>Ustawienia & MCP</span>}
            </button>
          </nav>

          {!sidebarCollapsed && (
            <div style={{ marginTop: 'auto', padding: '12px', background: '#050811', borderRadius: '8px', border: '1px solid #1e293b', fontSize: '11px', color: '#94a3b8' }}>
              <strong>Profil Aktywny:</strong>
              <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)} style={{ width: '100%', marginTop: '6px', padding: '4px', background: '#0b1322', color: '#fff', border: '1px solid #1e293b', borderRadius: '4px' }}>
                <option value="astyria">Astyria</option>
                <option value="aria">Aria</option>
                <option value="aurelius">Aurelius</option>
                <option value="alex">Alex</option>
              </select>
            </div>
          )}
        </aside>

        {/* Content Area */}
        <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#050811' }}>
          {/* Chat Tab */}
          {activeTab === 'chat' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '20px' }}>
              <div style={{ marginBottom: '12px' }}>
                <h2 style={{ margin: '0 0 4px 0', fontSize: '18px' }}>Duży Czat AI (Styl ChatGPT)</h2>
                <p style={{ margin: 0, fontSize: '12px', color: '#94a3b8' }}>Rozmowa wymaga rzeczywistego połączenia z wybranym agentem. Profil nie nadaje uprawnień root.</p>
              </div>
              <div ref={transcriptRef} style={{ flex: 1, overflowY: 'auto', padding: '16px', background: '#0b1322', border: '1px solid #1e293b', borderRadius: '12px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
                {messages.map((m, idx) => (
                  <div key={idx} style={{ padding: '12px 16px', borderRadius: '8px', background: m.role === 'user' ? 'rgba(56,189,248,0.1)' : 'rgba(255,255,255,0.03)', border: '1px solid #1e293b', maxWidth: '800px', alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', width: '100%' }}>
                    <div style={{ fontSize: '11px', color: '#38bdf8', marginBottom: '4px', fontWeight: 600 }}>{m.role === 'user' ? 'Ty' : m.agent}</div>
                    <div style={{ fontSize: '14px', lineHeight: '1.5' }}>{m.text}</div>
                    <div style={{ display: 'flex', gap: '8px', marginTop: '8px', fontSize: '12px', alignItems: 'center' }}>
                      <button onClick={() => setMessages(msgs => msgs.map((msg, i) => i === idx ? { ...msg, feedback: '👍 Pozytywna opinia' } : msg))} style={{ background: 'none', border: '1px solid #1e293b', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', color: '#cbd5e1' }}>👍</button>
                      <button onClick={() => setMessages(msgs => msgs.map((msg, i) => i === idx ? { ...msg, feedback: '👎 Zgłoszono uwagę' } : msg))} style={{ background: 'none', border: '1px solid #1e293b', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', color: '#cbd5e1' }}>👎</button>
                      {m.feedback && <span style={{ fontSize: '11px', color: '#34d399', marginLeft: '6px' }}>{m.feedback}</span>}
                    </div>
                  </div>
                ))}
              </div>
              <form onSubmit={handleSend} style={{ display: 'flex', gap: '10px', marginTop: '16px' }}>
                <input value={inputMsg} onChange={e => setInputMsg(e.target.value)} placeholder="Napisz wiadomość do Astyrii lub innego agenta..." style={{ flex: 1, padding: '12px 16px', background: '#0b1322', border: '1px solid #1e293b', borderRadius: '10px', color: '#fff', fontSize: '14px' }} />
                <button type="submit" style={{ padding: '0 20px', background: '#38bdf8', color: '#050811', border: 'none', borderRadius: '10px', fontWeight: 600, cursor: 'pointer' }}>Wyślij</button>
              </form>
            </div>
          )}

          {/* ChatGPT Work & Canvas Tab */}
          {activeTab === 'work' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '20px', overflowY: 'auto' }}>
              <h2 style={{ margin: '0 0 4px 0', fontSize: '18px' }}>ChatGPT Work & Canvas Workspace</h2>
              <p style={{ margin: '0 0 16px 0', fontSize: '12px', color: '#94a3b8' }}>Zarządzanie plikami projektu oraz zadaniami kanwy w stylu ChatGPT.</p>
              <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: '20px' }}>
                <div style={{ background: '#0b1322', border: '1px solid #1e293b', borderRadius: '12px', padding: '16px' }}>
                  <h3 style={{ fontSize: '13px', color: '#38bdf8', marginTop: 0 }}>Pliki Kanwy</h3>
                  <ul style={{ listStyle: 'none', padding: 0, margin: 0, fontSize: '13px' }}>
                    <li style={{ padding: '6px 8px', background: 'rgba(56,189,248,0.1)', borderRadius: '6px', color: '#38bdf8', marginBottom: '4px' }}>📄 bastilla_shan_theme.css</li>
                    <li style={{ padding: '6px 8px', color: '#94a3b8', marginBottom: '4px' }}>📄 cosmic_acquisitions.yaml</li>
                    <li style={{ padding: '6px 8px', color: '#94a3b8', marginBottom: '4px' }}>📄 mcp_bridge_spec.json</li>
                  </ul>
                </div>
                <div style={{ background: '#0b1322', border: '1px solid #1e293b', borderRadius: '12px', padding: '20px' }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: '14px' }}>Rejestr Zadań w Kanwie</h3>
                  <p style={{ fontSize: '12px', color: '#34d399', margin: '0 0 16px 0' }}>● Wszystkie zadania i pliki są zsynchronizowane z pamięcią API.</p>
                  <div style={{ padding: '12px', background: '#050811', borderRadius: '8px', border: '1px solid #1e293b', marginBottom: '8px' }}>
                    <strong>Implementacja motywu Bastilla Shan</strong>
                    <p style={{ fontSize: '12px', color: '#94a3b8', margin: '4px 0 0 0' }}>Agent: Astyria · Status: Zrealizowano</p>
                  </div>
                  <div style={{ padding: '12px', background: '#050811', borderRadius: '8px', border: '1px solid #1e293b' }}>
                    <strong>Akwizycja Wszechświata (Wszystko poza Ziemią)</strong>
                    <p style={{ fontSize: '12px', color: '#94a3b8', margin: '4px 0 0 0' }}>Agent: Aurelius · Status: Zablokowano Ziemię</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Terminal Tab */}
          {activeTab === 'terminal' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '20px' }}>
              <h2 style={{ margin: '0 0 4px 0', fontSize: '18px' }}>Terminal API</h2>
              <p style={{ margin: '0 0 16px 0', fontSize: '12px', color: '#94a3b8' }}>Bezpieczne polecenia systemowe przez API (pwd, ls, cat).</p>
              <div style={{ flex: 1, background: '#0b1322', border: '1px solid #1e293b', borderRadius: '12px', padding: '16px', fontFamily: 'monospace', fontSize: '13px', color: '#38bdf8', overflowY: 'auto' }}>
                $ pwd<br/>/app/applet<br/>$ ls -la<br/>total 64<br/>drwxr-xr-x 1 root root 4096 debian13/<br/>drwxr-xr-x 1 root root 4096 src/<br/>-rw-r--r-- 1 root root 1024 server.ts
              </div>
            </div>
          )}

          {/* Four Agents Tab */}
          {activeTab === 'fourAgents' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '20px', overflowY: 'auto' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <div>
                  <h2 style={{ margin: '0 0 4px 0', fontSize: '18px' }}>Dwukierunkowa Debata 4 Agentów</h2>
                  <p style={{ margin: 0, fontSize: '12px', color: '#94a3b8' }}>Swobodna dyskusja wymaga działającej bramy czterech agentów.</p>
                </div>
                <button onClick={startDebate} disabled={isDebating} style={{ padding: '8px 16px', background: '#fbbf24', color: '#050811', border: 'none', borderRadius: '8px', fontWeight: 600, cursor: 'pointer' }}>{isDebating ? 'Sprawdzanie...' : 'Sprawdź gotowość'}</button>
              </div>
              <div style={{ marginBottom: '16px' }}>
                <label style={{ fontSize: '12px', display: 'block', marginBottom: '4px' }}>Temat debaty:</label>
                <input value={debateTopic} onChange={e => setDebateTopic(e.target.value)} style={{ width: '100%', padding: '8px', background: '#0b1322', color: '#fff', border: '1px solid #1e293b', borderRadius: '6px' }} />
              </div>
              <div style={{ flex: 1, background: '#0b1322', border: '1px solid #1e293b', borderRadius: '12px', padding: '16px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {debateLog.length === 0 && <p style={{ color: '#94a3b8', fontSize: '13px' }}>Sprawdź, czy dostępna jest rzeczywista brama czterech agentów.</p>}
                {debateLog.map((l, i) => (
                  <div key={i} style={{ padding: '10px 14px', borderLeft: `3px solid ${l.color}`, background: 'rgba(255,255,255,0.02)', borderRadius: '6px' }}>
                    <strong style={{ color: l.color }}>{l.name}</strong>: <span style={{ color: '#e2e8f0' }}>{l.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Astyria Design Tab */}
          {activeTab === 'astyria' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '20px', overflowY: 'auto' }}>
              <h2 style={{ margin: '0 0 4px 0', fontSize: '18px' }}>Astyria Design & Akwizycja Kosmiczna</h2>
              <p style={{ margin: '0 0 16px 0', fontSize: '12px', color: '#94a3b8' }}>Zakup całego wszechświata poza Ziemią w celu zasilenia interfejsu Debian 13.</p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '16px' }}>
                {cosmicAssets.map((asset, i) => (
                  <div key={i} style={{ background: '#0b1322', border: asset.locked ? '2px dashed #ef4444' : '1px solid #1e293b', borderRadius: '12px', padding: '16px' }}>
                    <h3 style={{ margin: '0 0 6px 0', fontSize: '15px' }}>{asset.name}</h3>
                    <p style={{ fontSize: '12px', color: asset.locked ? '#ef4444' : '#34d399', fontWeight: 600, margin: '0 0 4px 0' }}>{asset.status}</p>
                    <p style={{ fontSize: '12px', color: '#94a3b8', margin: 0 }}>{asset.role}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Settings Tab */}
          {activeTab === 'settings' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '20px', overflowY: 'auto' }}>
              <h2 style={{ margin: '0 0 4px 0', fontSize: '18px' }}>Ustawienia Systemu & MCP</h2>
              <p style={{ margin: '0 0 16px 0', fontSize: '12px', color: '#94a3b8' }}>Diagnostyka API, integracja MCP oraz GPT Actions.</p>
              <div style={{ background: '#0b1322', border: '1px solid #1e293b', borderRadius: '12px', padding: '20px' }}>
                <h3 style={{ margin: '0 0 12px 0', fontSize: '14px', color: '#38bdf8' }}>Publiczny Serwer HTTPS / Tunnel</h3>
                <p style={{ fontSize: '13px', margin: '0 0 8px 0' }}><strong>Endpoint MCP:</strong> <code>https://pharmacy-arguments-mere-landing.trycloudflare.com/mcp</code></p>
                <p style={{ fontSize: '13px', margin: '0 0 8px 0' }}><strong>Schemat OpenAPI:</strong> <a href="/gpt-actions.openapi.json" target="_blank" style={{ color: '#38bdf8' }}>/gpt-actions.openapi.json</a></p>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
