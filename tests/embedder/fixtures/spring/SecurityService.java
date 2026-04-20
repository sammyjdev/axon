package com.example.security;
import org.springframework.security.core.userdetails.*;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import java.util.*;
@Service
public class SecurityService implements UserDetailsService {
    private final PasswordEncoder encoder;
    private final Map<String, String> users = new HashMap<>();
    public SecurityService(PasswordEncoder encoder) { this.encoder = encoder; }
    @Override
    public UserDetails loadUserByUsername(String username) throws UsernameNotFoundException {
        String password = users.get(username);
        if (password == null) throw new UsernameNotFoundException("User not found: " + username);
        return User.withUsername(username).password(password).roles("USER").build();
    }
    public void registerUser(String username, String rawPassword) {
        users.put(username, encoder.encode(rawPassword));
    }
    public boolean verifyPassword(String raw, String encoded) {
        return encoder.matches(raw, encoded);
    }
    public UserDetails loadAdmin(String username) {
        return User.withUsername(username).password("{noop}admin").roles("USER", "ADMIN").build();
    }
}
