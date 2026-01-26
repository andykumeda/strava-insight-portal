/** @type {import('tailwindcss').Config} */
export default {
    darkMode: 'class',
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    safelist: [
        'p-2', 'py-2', 'px-2', 'gap-1.5', 'gap-2', 'mb-2', 'mt-1.5', 'mt-2',
        'sm:p-4', 'sm:py-8', 'sm:px-6', 'sm:gap-4', 'sm:mb-6', 'sm:mt-4'
    ],
    theme: {
        extend: {
            typography: ({ theme }) => ({
                DEFAULT: {
                    css: {
                        a: {
                            color: theme('colors.orange.600'),
                            textDecoration: 'underline',
                            fontWeight: '600',
                            '&:hover': {
                                color: theme('colors.orange.700'),
                            },
                        },
                    },
                },
                invert: {
                    css: {
                        a: {
                            color: theme('colors.orange.400'),
                            '&:hover': {
                                color: theme('colors.orange.300'),
                            },
                        },
                    },
                },
            }),
        },
    },
    plugins: [require('@tailwindcss/typography')],
}
