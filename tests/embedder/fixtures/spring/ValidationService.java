package com.example.service;
import org.springframework.stereotype.Service;
import java.util.*;
@Service
public class ValidationService {
    public boolean isValidEmail(String email) { return email != null && email.contains("@"); }
    public void validateAge(int age) {
        if (age < 18 || age > 120) throw new IllegalArgumentException("Invalid age: " + age);
        System.out.println("Valid age: " + age);
    }
    public void validateName(String name) {
        if (name == null || name.isBlank() || name.length() < 2 || name.length() > 50)
            throw new IllegalArgumentException("Invalid name");
        System.out.println("Valid name: " + name);
    }
    public List<String> validateAll(Map<String, Object> fields) {
        List<String> errors = new ArrayList<>();
        fields.forEach((k, v) -> {
            if (v == null) errors.add(k + " is required");
        });
        return errors;
    }
}
