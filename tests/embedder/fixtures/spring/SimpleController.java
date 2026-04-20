package com.example.controller;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/orders")
public class SimpleController {
    private final Object svc;
    @GetMapping("/{id}")
    public Object getOrder(@PathVariable Long id) {
        return null;
    }

    @PostMapping
    public Object createOrder(@RequestBody Object req) {
        return req;
    }

    @DeleteMapping("/{id}")
    public void deleteOrder(@PathVariable Long id) {
        System.out.println("deleted: " + id);
    }
}
