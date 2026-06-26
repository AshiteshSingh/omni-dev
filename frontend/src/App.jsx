import React, { useState, useRef, useEffect } from 'react';
import { Terminal, Send, Database, Trash2, User, Bot, Activity } from 'lucide-react';
import './index.css';

function App() {
  const [messages, setMessages] = useState([
    { role: 'agent', text: "Initialization complete. I am Omni-Dev, your context-aware coding agent. My long-term memory graph is active. What are we building today?" }
  ]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [memoryContext, setMemoryContext] = useState("Memory systems online. Context will populate here as I learn about your project.");
  const chatEndRef = useRef(null);

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  const handleSend = async () => {
    if (!input.trim()) return;
    
    const userMsg = input.trim();
    setInput('');
    setMessages(prev => [...prev, { role: 'user', text: userMsg }]);
    setIsTyping(true);

    try {
      // 1. Fetch response from Omni-Dev
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg })
      });
      const data = await res.json();
      
      setMessages(prev => [...prev, { role: 'agent', text: data.response || data.error || "No response received." }]);
      
      // 2. Fetch updated context from memory
      const memRes = await fetch(`/api/memory?query=${encodeURIComponent(userMsg)}`);
      const memData = await memRes.json();
      if (memData.context && memData.context !== "Nothing recalled for this query.") {
        setMemoryContext(memData.context);
      }
      
    } catch (err) {
      setMessages(prev => [...prev, { role: 'agent', text: `Connection error: ${err.message}` }]);
    } finally {
      setIsTyping(false);
    }
  };

  const handleClearMemory = async () => {
    try {
      await fetch('/api/memory/clear', { method: 'POST' });
      setMemoryContext("Memory wiped. System amnesia induced.");
      setMessages(prev => [...prev, { role: 'agent', text: "Who am I? Where am I? (Memory cleared)" }]);
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="app-container">
      {/* Main Chat Interface */}
      <main className="glass-panel chat-section">
        <header className="header">
          <div className="title-area">
            <Terminal className="logo-icon" size={20} />
            <h1>Omni-Dev</h1>
          </div>
          <div className="status-badge">
            <div className="status-dot"></div>
            System Online
          </div>
        </header>

        <div className="chat-history">
          {messages.map((msg, i) => (
            <div key={i} className={`message-wrapper ${msg.role}`}>
              <div className={`avatar ${msg.role}`}>
                {msg.role === 'user' ? <User size={18} /> : <Bot size={18} color="#0070F3" />}
              </div>
              <div className="message">
                {msg.text}
              </div>
            </div>
          ))}
          {isTyping && (
            <div className="message-wrapper agent">
              <div className="avatar agent">
                <Bot size={18} color="#0070F3" />
              </div>
              <div className="message">
                <div className="typing-indicator">
                  <div className="dot"></div>
                  <div className="dot"></div>
                  <div className="dot"></div>
                </div>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        <div className="input-area">
          <div className="input-wrapper">
            <input
              type="text"
              placeholder="Ask Omni-Dev to build something or state a project preference..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              disabled={isTyping}
            />
            <button className="send-btn" onClick={handleSend} disabled={!input.trim() || isTyping}>
              <Send size={18} />
            </button>
          </div>
        </div>
      </main>

      {/* Context / Memory Visualizer */}
      <aside className="glass-panel context-section">
        <div className="context-header">
          <div style={{display: 'flex', alignItems: 'center', gap: '0.5rem'}}>
            <Database size={16} className="logo-icon" style={{color: '#888'}} />
            <h2>Knowledge Graph</h2>
          </div>
          <button className="clear-btn" onClick={handleClearMemory} title="Simulate AI Amnesia">
            <Trash2 size={16} />
          </button>
        </div>
        
        <div className="memory-graph">
          <div className="memory-node">
            {memoryContext.split('\n').map((line, i) => (
              <React.Fragment key={i}>
                {line}
                <br />
              </React.Fragment>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}

export default App;
