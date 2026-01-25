import React, { useState, useEffect } from 'react';
import { Send, User as UserIcon, Activity, RefreshCw } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import type { User, Message } from '../types';
import { API_ENDPOINTS } from '../config';
import ThemeToggle from './ThemeToggle'; // Import ThemeToggle

interface DashboardProps {
    user: User;
}

const Dashboard: React.FC<DashboardProps> = ({ user }) => {
    const [input, setInput] = useState('');
    const [messages, setMessages] = useState<Message[]>([]);
    const [loading, setLoading] = useState(false);
    const [history, setHistory] = useState<string[]>([]);
    const [historyIndex, setHistoryIndex] = useState(-1);
    const [syncStats, setSyncStats] = useState<{ synced: number, enriched: number, percent: number } | null>(null);

    useEffect(() => {
        const fetchStatus = async () => {
            try {
                const res = await fetch(API_ENDPOINTS.STATUS, { credentials: 'include' });
                if (res.ok) {
                    const data = await res.json();
                    if (data.status === 'online' && data.sync) {
                        setSyncStats({
                            synced: data.sync.synced_activities,
                            enriched: data.sync.enriched_activities,
                            percent: data.sync.percent
                        });
                    }
                }
            } catch (e) {
                console.error("Status check failed", e);
            }
        };

        // Initial fetch
        fetchStatus();

        // Poll every 10 seconds to keep updated during hydration
        const interval = setInterval(fetchStatus, 10000);
        return () => clearInterval(interval);
    }, []);

    const submitQuestion = async (text: string) => {
        if (!text.trim()) return;

        // Add to history if different from last entry
        setHistory(prev => {
            if (prev.length > 0 && prev[prev.length - 1] === text) return prev;
            return [...prev, text];
        });
        setHistoryIndex(-1); // Reset history pointer

        const userMsg: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: text,
            timestamp: new Date()
        };
        setMessages(prev => [...prev, userMsg]);
        setLoading(true);

        try {
            const res = await fetch(API_ENDPOINTS.QUERY, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ question: text }),
                credentials: 'include' // Important for cookies!
            });

            if (!res.ok) {
                throw new Error(`Error: ${res.statusText}`);
            }

            const data = await res.json();
            const aiMsg: Message = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: data.answer,
                timestamp: new Date(),
                data: data.data_used
            };
            setMessages(prev => [...prev, aiMsg]);

        } catch (err) {
            const errorMsg: Message = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: "Sorry, I encountered an error answering your question. " + String(err),
                timestamp: new Date()
            };
            setMessages(prev => [...prev, errorMsg]);
        } finally {
            setLoading(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            if (!loading && input.trim()) {
                handleSend();
            }
            return;
        }

        if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (history.length === 0) return;

            const newIndex = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1);
            setHistoryIndex(newIndex);
            setInput(history[newIndex]);
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (history.length === 0 || historyIndex === -1) return;

            const newIndex = historyIndex + 1;
            if (newIndex >= history.length) {
                setHistoryIndex(-1);
                setInput('');
            } else {
                setHistoryIndex(newIndex);
                setInput(history[newIndex]);
            }
        }
    };

    const handleSend = () => {
        if (!input.trim()) return;
        submitQuestion(input);
        setInput('');
    };

    return (
        <div className="flex flex-col h-screen bg-gray-50 dark:bg-gray-900 transition-colors duration-200">
            {/* Header */}
            <header className="bg-white dark:bg-gray-800 border-b dark:border-gray-700 px-6 py-4 flex items-center justify-between shadow-sm transition-colors duration-200">
                <div className="flex items-center gap-2">
                    <Activity className="w-6 h-6 text-orange-600" />
                    <h1 className="text-xl font-bold text-gray-800 dark:text-white">ActivityCopilot</h1>
                </div>
                <div className="flex items-center gap-4">
                    {syncStats && (
                        <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 px-2.5 py-1.5 rounded-full" title={`${syncStats.enriched} / ${syncStats.synced} activities enriched`}>
                            <RefreshCw className={`w-3 h-3 ${syncStats.percent < 100 ? 'animate-spin' : ''}`} />
                            <span className="font-medium">{syncStats.percent}% Synced</span>
                        </div>
                    )}
                    <ThemeToggle />
                    <div className="flex items-center gap-2">
                        {user.profile_picture ? (
                            <img src={user.profile_picture} alt={user.name} className="w-8 h-8 rounded-full" />
                        ) : (
                            <div className="w-8 h-8 bg-gray-200 dark:bg-gray-600 rounded-full flex items-center justify-center">
                                <UserIcon className="w-5 h-5 text-gray-500 dark:text-gray-300" />
                            </div>
                        )}
                        <span className="font-medium text-gray-700 dark:text-gray-200">{user.name}</span>
                    </div>
                </div>
            </header>

            {/* Chat Area */}
            <div className="flex-1 overflow-y-auto p-4 md:p-8">
                <div className="max-w-3xl mx-auto space-y-6">
                    {messages.length === 0 && (
                        <div className="text-center py-10">
                            <h2 className="text-2xl font-bold text-gray-800 dark:text-white mb-2">Welcome, {user.name}!</h2>
                            <p className="text-gray-600 dark:text-gray-400 mb-8">Ask me anything about your Strava activities.</p>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-left">
                                <button onClick={() => submitQuestion("What was my weekly mileage for the last 4 weeks?")} className="p-4 bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 hover:border-orange-500 hover:shadow-sm transition-all text-gray-700 dark:text-gray-200">
                                    "What was my weekly mileage for the last 4 weeks?"
                                </button>
                                <button onClick={() => submitQuestion("Compare my running pace this year vs last year.")} className="p-4 bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 hover:border-orange-500 hover:shadow-sm transition-all text-gray-700 dark:text-gray-200">
                                    "Compare my running pace this year vs last year."
                                </button>
                            </div>
                        </div>
                    )}

                    {messages.map(msg => (
                        <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                            <div className={`max-w-xl rounded-2xl px-6 py-4 transition-colors duration-200 ${msg.role === 'user'
                                ? 'bg-orange-600 text-white rounded-br-none'
                                : 'bg-white dark:bg-gray-800 border dark:border-gray-700 text-gray-800 dark:text-gray-100 rounded-bl-none shadow-sm'
                                }`}>
                                {msg.role === 'user' ? (
                                    <div className="whitespace-pre-wrap">{msg.content}</div>
                                ) : (
                                    <div className="prose prose-sm max-w-none dark:prose-invert">
                                        <ReactMarkdown
                                            components={{
                                                a: ({ node, ...props }) => (
                                                    <a
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="inline-flex items-center gap-0.5 text-orange-700 dark:text-orange-400 font-bold hover:text-orange-800 dark:hover:text-orange-300 transition-colors bg-orange-100 dark:bg-orange-900/30 px-1.5 py-0.5 rounded text-xs sm:text-sm no-underline hover:underline"
                                                        {...props}
                                                    />
                                                )
                                            }}
                                        >
                                            {msg.content}
                                        </ReactMarkdown>
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}

                    {loading && (
                        <div className="flex justify-start">
                            <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 px-6 py-4 rounded-2xl rounded-bl-none shadow-sm transition-colors duration-200">
                                <div className="flex items-center gap-3">
                                    <div className="flex items-center gap-1">
                                        <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" />
                                        <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce delay-100" />
                                        <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce delay-200" />
                                    </div>
                                    <span className="text-sm text-gray-500 dark:text-gray-400">Fetching activity details... this may take a moment.</span>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Input Area */}
            <div className="p-4 bg-white dark:bg-gray-800 border-t dark:border-gray-700 transition-colors duration-200">
                <div className="max-w-3xl mx-auto flex gap-2">
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Ask about your activities..."
                        className="flex-1 p-3 border dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-500 dark:placeholder-gray-400 transition-colors duration-200"
                    />
                    <button
                        onClick={handleSend}
                        disabled={loading || !input.trim()}
                        className="bg-orange-600 text-white p-3 rounded-lg hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                        <Send className="w-5 h-5" />
                    </button>
                </div>
            </div>

        </div>
    );
};

export default Dashboard;
