package com.example.service;
import org.springframework.stereotype.Service;
import java.util.Comparator;
import java.util.List;
@Service
public class AnonymousClassService {
    public Runnable createTask(String name) {
        return new Runnable() {
            @Override
            public void run() {
                System.out.println("Running: " + name);
            }
        };
    }
    public List<String> sortNames(List<String> names) {
        names.sort(new Comparator<String>() {
            @Override
            public int compare(String a, String b) {
                return a.compareToIgnoreCase(b);
            }
        });
        return names;
    }
    public void execute() {
        Runnable task = new Runnable() {
            @Override
            public void run() {
                System.out.println("inline task");
            }
        };
        task.run();
    }
}
