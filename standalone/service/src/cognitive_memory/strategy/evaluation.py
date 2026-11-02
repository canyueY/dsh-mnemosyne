"""Off-policy evaluation using logged action-selection probabilities."""
import math


def evaluate_policy(records, *, min_samples=30, min_effective_samples=5):
    rows=list(records)
    def insufficient(reason):
        return {"status":"insufficient","reason":reason,"n":len(rows),"deploy_allowed":False}
    if len(rows) < min_samples:
        return insufficient("sample_count")
    weights=[];rewards=[];dr=[];baseline=[]
    for row in rows:
        actions=row.get("available_actions") or []
        behavior=row.get("behavior_policy") or {}
        target=row.get("target_policy") or {}
        action=row.get("selected_action")
        if not actions or len(set(actions)) != len(actions) or action not in actions:
            return insufficient("action_space_missing")
        for policy in (behavior,target):
            if set(policy) != set(actions):
                return insufficient("policy_distribution_missing")
            if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 or v>1 for v in policy.values()):
                return insufficient("invalid_probability")
            if not math.isclose(sum(policy.values()),1.0,abs_tol=1e-8):
                return insufficient("unnormalized_policy")
        if any(target[a]>0 and behavior[a]<=0 for a in actions):
            return insufficient("no_overlap")
        p=behavior[action]
        if p<=0:
            return insufficient("zero_logged_propensity")
        if row.get("propensity") is not None and not math.isclose(row["propensity"],p,abs_tol=1e-8):
            return insufficient("propensity_mismatch")
        reward=row.get("reward")
        if not isinstance(reward,(int,float)) or not math.isfinite(reward):
            return insufficient("reward_missing")
        w=target[action]/p
        weights.append(w);rewards.append(float(reward))
        q=row.get("q_values") or {}
        if set(q)==set(actions) and all(isinstance(v,(int,float)) and math.isfinite(v) for v in q.values()):
            dr.append(sum(target[a]*q[a] for a in actions)+w*(reward-q[action]))
        bp=row.get("baseline_policy") or {}
        if set(bp)==set(actions) and all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in bp.values()) and math.isclose(sum(bp.values()),1.0,abs_tol=1e-8):
            if any(bp[a]>0 and behavior[a]<=0 for a in actions):
                return insufficient("baseline_no_overlap")
            baseline.append(bp[action]/p*reward)
    total=sum(weights); squares=sum(w*w for w in weights)
    ess=total*total/squares if squares else 0
    if total<=0 or ess<min_effective_samples:
        return insufficient("effective_sample_size")
    n=len(rows)
    ips=sum(w*r for w,r in zip(weights,rewards))/n
    return {"status":"evaluated","n":n,"ips":ips,
            "snips":sum(w*r for w,r in zip(weights,rewards))/total,
            "dr":sum(dr)/n if len(dr)==n else None,
            "dr_status":"evaluated" if len(dr)==n else "q_values_missing",
            "baseline_ips":sum(baseline)/n if len(baseline)==n else None,
            "effective_sample_size":ess,"max_weight":max(weights),
            "deploy_allowed":False,"method_version":"ope.v1"}
