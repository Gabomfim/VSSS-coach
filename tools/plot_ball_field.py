"""Plot the controller's shared IFAC'08 ball univector as standalone SVG."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from vsss_coach.fields import univector_ball_field
from vsss_coach.model import BallState, RobotState, Vec2

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ball-x", type=float, default=0.05)
    parser.add_argument("--ball-y", type=float, default=0.18)
    parser.add_argument("--goal-x", type=float, default=0.75)
    parser.add_argument("--goal-y", type=float, default=0.0)
    parser.add_argument("--spiral-radius", type=float, default=0.11)
    parser.add_argument("--spiral-smoothing", type=float, default=0.08)
    parser.add_argument("--invert", action="store_true")
    args = parser.parse_args()
    width, height, margin = 900, 900, 70
    x_min, x_max, y_min, y_max = -0.78, 0.94, -0.68, 0.68
    def screen(point: Vec2) -> tuple[float, float]:
        return (margin+(point.x-x_min)/(x_max-x_min)*(width-2*margin), height-margin-(point.y-y_min)/(y_max-y_min)*(height-2*margin))
    ball, goal = BallState(position=Vec2(args.ball_x, args.ball_y)), Vec2(args.goal_x, args.goal_y)
    parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10z" fill="#ffd54a"/></marker><marker id="axis" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#54f0ff"/></marker></defs><rect width="100%" height="100%" fill="#10251a"/><rect x="{margin}" y="{margin}" width="{width-2*margin}" height="{height-2*margin}" fill="#143d25" stroke="white" stroke-width="3"/><text x="{width/2}" y="38" fill="white" font-family="sans-serif" font-size="18" text-anchor="middle">Campo univetorial da bola apontado ao gol inimigo</text>''']
    for row in range(25):
        y = y_min+(y_max-y_min)*row/24
        for column in range(29):
            x = x_min+(x_max-x_min)*column/28
            v = univector_ball_field(RobotState(position=Vec2(x,y)), ball, goal, args.spiral_radius, args.spiral_smoothing)
            if args.invert:
                v = v * -1.0
            sx, sy = screen(Vec2(x,y)); length = 18.0
            parts.append(f'<line x1="{sx:.2f}" y1="{sy:.2f}" x2="{sx+v.x*length:.2f}" y2="{sy-v.y*length:.2f}" stroke="#ffd54a" stroke-width="2.1" marker-end="url(#arrow)"/>')
    bx, by = screen(ball.position); gx, gy = screen(goal)
    post_top = screen(Vec2(args.goal_x, 0.20)); post_bottom = screen(Vec2(args.goal_x, -0.20)); goal_back_top = screen(Vec2(0.88, 0.20)); goal_back_bottom = screen(Vec2(0.88, -0.20))
    parts += [f'<path d="M{post_top[0]} {post_top[1]} L{goal_back_top[0]} {goal_back_top[1]} L{goal_back_bottom[0]} {goal_back_bottom[1]} L{post_bottom[0]} {post_bottom[1]}" fill="none" stroke="#ff4d67" stroke-width="7"/><line x1="{post_top[0]}" y1="{post_top[1]}" x2="{post_bottom[0]}" y2="{post_bottom[1]}" stroke="white" stroke-width="3" stroke-dasharray="8 7"/><circle cx="{post_top[0]}" cy="{post_top[1]}" r="9" fill="#ff4d67"/><circle cx="{post_bottom[0]}" cy="{post_bottom[1]}" r="9" fill="#ff4d67"/><text x="{goal_back_top[0]-5}" y="{goal_back_top[1]-18}" fill="#ff9aaa" font-family="sans-serif" font-size="18" text-anchor="end">GOL INIMIGO</text>', f'<line x1="{bx}" y1="{by}" x2="{gx}" y2="{gy}" stroke="#54f0ff" stroke-width="3" stroke-dasharray="9 7" marker-end="url(#axis)"/>', f'<circle cx="{bx}" cy="{by}" r="11" fill="white" stroke="#222" stroke-width="3"/><text x="{bx+16}" y="{by-14}" fill="white" font-family="sans-serif" font-size="17">bola ({args.ball_x:.2f}, {args.ball_y:.2f})</text>', f'<path d="M{gx-13} {gy}L{gx} {gy-13}L{gx+13} {gy}L{gx} {gy+13}Z" fill="#ff4d67"/><text x="{gx-12}" y="{gy+34}" fill="#ff9aaa" font-family="sans-serif" font-size="17" text-anchor="end">centro do gol</text>', f'<text x="{(bx+gx)/2}" y="{(by+gy)/2-12}" fill="#54f0ff" font-family="sans-serif" font-size="17" text-anchor="middle">direção desejada do chute = unit(gol − bola)</text>', f'<text x="{margin+12}" y="{height-margin-14}" fill="white" font-family="monospace" font-size="16">eixo canônico = −direção do chute   dₑ = {args.spiral_radius:.3f} m   Kᵣ = {args.spiral_smoothing:.3f} m</text>', '</svg>']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(parts), encoding="utf-8")

if __name__ == "__main__":
    main()
