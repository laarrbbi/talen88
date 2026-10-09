import { avatarFill, initials } from "../lib/avatar";

/**
 * Employee avatar: the real uploaded photo when we have one, otherwise a deterministic
 * colored disc with the person's initials. `name` is only used for the fallback initials
 * (it is already resolved through the identity boundary by the caller).
 */
export function Avatar({ token, name, photo, size = 28 }: {
  token: string; name?: string | null; photo?: string | null; size?: number;
}) {
  const radius = Math.round(size * 0.3);
  if (photo) {
    return (
      <span className="avatar2" style={{ width: size, height: size, borderRadius: radius }}>
        <img src={photo} alt="" />
      </span>
    );
  }
  return (
    <span
      className="avatar2"
      style={{
        width: size, height: size, borderRadius: radius,
        background: avatarFill(token), color: "#fff",
        fontSize: Math.round(size * 0.4), fontWeight: 600,
      }}
    >
      {name ? initials(name) : ""}
    </span>
  );
}
