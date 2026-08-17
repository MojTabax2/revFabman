import { Suspense } from 'react';
import LoginForm from './LoginForm';

export default function LoginPage() {
  // useSearchParams() (in LoginForm) requires a Suspense boundary, otherwise
  // the prerender of this route fails at build time.
  return (
    <Suspense fallback={<div className="login-wrap" />}>
      <LoginForm />
    </Suspense>
  );
}
