import React, { useState } from 'react';
import { Send, User as UserIcon } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import type { User, Message } from '../types';
import { API_ENDPOINTS } from '../config';

interface DashboardProps {
    user: User;
}

const Dashboard: React.FC<DashboardProps> = ({ user }) => {
    const [input, setInput] = useState('');
    const [messages, setMessages] = useState<Message[]>([]);
    const [loading, setLoading] = useState(false);
    const [history, setHistory] = useState<string[]>([]);
    const [historyIndex, setHistoryIndex] = useState(-1);

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
        <div className="flex flex-col h-screen bg-gray-50">
            {/* Header */}
            <header className="bg-white border-b px-6 py-4 flex items-center justify-between shadow-sm">
                <h1 className="text-xl font-bold text-gray-800">Strava Insight Portal</h1>
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                        {user.profile_picture ? (
                            <img src={user.profile_picture} alt={user.name} className="w-8 h-8 rounded-full" />
                        ) : (
                            <div className="w-8 h-8 bg-gray-200 rounded-full flex items-center justify-center">
                                <UserIcon className="w-5 h-5 text-gray-500" />
                            </div>
                        )}
                        <span className="font-medium text-gray-700">{user.name}</span>
                    </div>
                </div>
            </header>

            {/* Chat Area */}
            <div className="flex-1 overflow-y-auto p-4 md:p-8">
                <div className="max-w-3xl mx-auto space-y-6">
                    {messages.length === 0 && (
                        <div className="text-center py-10">
                            <h2 className="text-2xl font-bold text-gray-800 mb-2">Welcome, {user.name}!</h2>
                            <p className="text-gray-600 mb-8">Ask me anything about your Strava activities.</p>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-left">
                                <button onClick={() => submitQuestion("What was my weekly mileage for the last 4 weeks?")} className="p-4 bg-white rounded-lg border hover:border-orange-500 hover:shadow-sm transition-all text-gray-700">
                                    "What was my weekly mileage for the last 4 weeks?"
                                </button>
                                <button onClick={() => submitQuestion("Compare my running pace this year vs last year.")} className="p-4 bg-white rounded-lg border hover:border-orange-500 hover:shadow-sm transition-all text-gray-700">
                                    "Compare my running pace this year vs last year."
                                </button>
                            </div>
                        </div>
                    )}

                    {messages.map(msg => (
                        <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                            <div className={`max-w-xl rounded-2xl px-6 py-4 ${msg.role === 'user'
                                ? 'bg-orange-600 text-white rounded-br-none'
                                : 'bg-white border text-gray-800 rounded-bl-none shadow-sm'
                                }`}>
                                {msg.role === 'user' ? (
                                    <div className="whitespace-pre-wrap">{msg.content}</div>
                                ) : (
                                    <div className="prose prose-sm max-w-none">
                                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}

                    {loading && (
                        <div className="flex justify-start">
                            <div className="bg-white border px-6 py-4 rounded-2xl rounded-bl-none shadow-sm flex items-center gap-2">
                                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-100" />
                                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-200" />
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Input Area */}
            <div className="p-4 bg-white border-t">
                <div className="max-w-3xl mx-auto flex gap-2">
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Ask about your activities..."
                        className="flex-1 p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500"
                        // Removed disabled={loading} to allow typing
                    />
                    <button
                        onClick={handleSend}
                        disabled={loading || !input.trim()}
                        className="bg-orange-600 text-white p-3 rounded-lg hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        <Send className="w-5 h-5" />
                    </button>
                </div>
            </div>

        </div>
    );
};

export default Dashboard;
