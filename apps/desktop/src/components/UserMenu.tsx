import { logout, type AuthUser } from '../lib/auth'
import Dropdown from './Dropdown'

// Avatar chip at the right of the header: who's signed in, and sign out.
export default function UserMenu({ user }: { user: AuthUser }): JSX.Element {
  const label = user.display_name ?? user.email
  const initial = label.trim().charAt(0).toUpperCase() || '?'

  return (
    <Dropdown
      menuClassName="dropdown-menu-down dropdown-menu-right user-menu"
      menuLabel="Account"
      trigger={({ open, toggle }) => (
        <button className="user-chip" onClick={toggle} aria-expanded={open} title={label}>
          {user.avatar_url ? (
            // Google avatars refuse requests that carry a foreign Referer.
            <img src={user.avatar_url} alt="" referrerPolicy="no-referrer" />
          ) : (
            <span>{initial}</span>
          )}
        </button>
      )}
    >
      <div className="user-menu-name">{label}</div>
      <div className="user-menu-email">
        {user.email}
        {user.is_superuser && <span className="user-menu-badge">admin</span>}
      </div>
      <button className="user-menu-signout" onClick={() => void logout()}>
        Sign out
      </button>
    </Dropdown>
  )
}
