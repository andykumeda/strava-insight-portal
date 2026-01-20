import React from 'react';
import { Activity } from 'lucide-react';
import { API_ENDPOINTS } from '../config';
import ThemeToggle from './ThemeToggle';

const LandingPage: React.FC = () => {
    const handleConnect = async () => {
        try {
            const response = await fetch(API_ENDPOINTS.AUTH.START, { method: 'POST' });
            const data = await response.json();
            if (data.url) {
                window.location.href = data.url;
            } else {
                alert('Failed to get auth URL');
            }
        } catch (error) {
            console.error(error);
            alert('Failed to connect to backend');
        }
    };

    return (
        <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex flex-col items-center justify-center p-4 transition-colors duration-200">
            <div className="absolute top-4 right-4">
                <ThemeToggle />
            </div>
            <div className="max-w-md w-full bg-white dark:bg-gray-800 rounded-xl shadow-lg p-8 text-center transition-colors duration-200">
                <div className="flex justify-center mb-6">
                    <img src="/logo.svg" alt="ActivityCopilot Logo" className="w-24 h-24 drop-shadow-lg" />
                </div>
                <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2 flex items-center justify-center gap-2">
                    <Activity className="w-8 h-8 text-orange-600" />
                    ActivityCopilot
                </h1>
                <p className="text-xl font-medium text-gray-700 dark:text-gray-200 mb-2">
                    Ask anything about your Strava training data.
                </p>
                <p className="text-gray-600 dark:text-gray-400 mb-8">
                    ActivityCopilot connects to your Strava account and lets you chat with your activity history.
                </p>
                <button
                    onClick={handleConnect}
                    className="w-full bg-orange-600 text-white font-semibold py-3 px-6 rounded-lg hover:bg-orange-700 transition-colors flex items-center justify-center gap-2"
                >
                    Connect with Strava
                </button>
            </div>
        </div>
    );
};

export default LandingPage;
